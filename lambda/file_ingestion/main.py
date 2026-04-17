"""
File Ingestion Service — FastAPI HTTP wrapper for Docker deployment.

Provides:
  - POST /api/v1/upload        — Direct file upload from frontend → S3 → process
  - GET /api/v1/jobs/{fileId}/stream — SSE stream for real-time pipeline status
  - GET /health                — Health check
  - POST /, POST /invoke       — Legacy Lambda invocation
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import boto3
import httpx
import uvicorn
from botocore.exceptions import ClientError
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from lambda_function import (
    compute_sha256,
    get_object_metadata,
    get_s3_client,
    process_s3_record,
    read_file_header,
)
from magic_numbers import detect_format

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
S3_BUCKET = os.environ.get("S3_BUCKET_NAME", "arch-ingestion-bucket")
JOB_PREFIX = "jobs"  # S3 key prefix: jobs/{fileId}/status.json
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Downstream service URLs (Docker internal DNS)
RASTER_EXTRACTION_URL = os.environ.get("RASTER_EXTRACTION_URL", "http://raster-line-extraction:8002")
DXF_EXTRACTION_URL = os.environ.get("DXF_EXTRACTION_URL", "http://dxf-geometry-extraction:8003")
DIMENSION_AUDIT_URL = os.environ.get("DIMENSION_AUDIT_URL", "http://dimension-audit:8080")
COMPLIANCE_URL = os.environ.get("COMPLIANCE_URL", "http://compliance-checker:8005")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("file_ingestion.api")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
logger.addHandler(_handler)
logger.propagate = False

# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(title="File Ingestion Service", version="2.0.0")

# ---------------------------------------------------------------------------
# Pipeline stage definitions — maps to Step Function stages
# ---------------------------------------------------------------------------
PIPELINE_STAGES = [
    "ingestion",
    "extraction",
    "spatial-linking",
    "math-audit",
    "compliance",
]


# ---------------------------------------------------------------------------
# S3 Job Status helpers
# ---------------------------------------------------------------------------
def _job_status_key(file_id: str) -> str:
    return f"{JOB_PREFIX}/{file_id}/status.json"


def _job_artifact_key(file_id: str, artifact: str) -> str:
    return f"{JOB_PREFIX}/{file_id}/{artifact}.json"


def write_job_status(file_id: str, status: dict) -> None:
    """Write job status to S3."""
    s3 = get_s3_client()
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=_job_status_key(file_id),
        Body=json.dumps(status, default=str).encode("utf-8"),
        ContentType="application/json",
    )


def read_job_status(file_id: str) -> Optional[dict]:
    """Read job status from S3. Returns None if not found."""
    s3 = get_s3_client()
    try:
        resp = s3.get_object(Bucket=S3_BUCKET, Key=_job_status_key(file_id))
        return json.loads(resp["Body"].read().decode("utf-8"))
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return None
        raise


def read_job_artifact(file_id: str, artifact: str) -> Optional[dict]:
    """Read a job artifact from S3. Returns None if not found."""
    s3 = get_s3_client()
    try:
        resp = s3.get_object(
            Bucket=S3_BUCKET, Key=_job_artifact_key(file_id, artifact)
        )
        return json.loads(resp["Body"].read().decode("utf-8"))
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return None
        raise


def check_artifact_exists(file_id: str, artifact: str) -> bool:
    """Check if an artifact exists in S3 without reading it."""
    s3 = get_s3_client()
    try:
        s3.head_object(Bucket=S3_BUCKET, Key=_job_artifact_key(file_id, artifact))
        return True
    except ClientError:
        return False


# ---------------------------------------------------------------------------
# Pipeline Orchestration — runs as background task after upload
# ---------------------------------------------------------------------------


def _update_stage(file_id: str, stage: str, status: str, **extra: Any) -> None:
    """Helper to write stage status to S3, merging with existing fields."""
    existing = read_job_status(file_id) or {}
    existing.update({
        "stage": stage,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **extra,
    })
    write_job_status(file_id, existing)


def _write_artifact(file_id: str, name: str, data: Any) -> None:
    """Write a pipeline artifact to S3 so the SSE stream can pick it up."""
    s3 = get_s3_client()
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=_job_artifact_key(file_id, name),
        Body=json.dumps(data, default=str).encode("utf-8"),
        ContentType="application/json",
    )


async def _run_pipeline(file_id: str, category: str, routing_message: dict) -> None:
    """
    Background task that runs the full processing pipeline:
      ingestion → extraction → spatial-linking → math-audit → compliance
    """
    base_url = RASTER_EXTRACTION_URL if category == "RASTER" else DXF_EXTRACTION_URL

    try:
        # ── Stage 2: Extraction ──────────────────────────────────────────
        _update_stage(file_id, "extraction", "PROCESSING")
        logger.info(f"[Pipeline:{file_id}] Starting extraction via {base_url}")

        sqs_event = {
            "Records": [{
                "body": json.dumps(routing_message),
                "messageId": f"direct-{file_id}",
            }]
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            ext_resp = await client.post(f"{base_url}/invoke", json=sqs_event)
            ext_resp.raise_for_status()
            ext_result = ext_resp.json()

        logger.info(f"[Pipeline:{file_id}] Extraction complete: {ext_result.get('status')}")

        # Extract coordinates from result
        results = ext_result.get("results", [])
        if not results or results[0].get("status") != "EXTRACTED":
            raise RuntimeError(f"Extraction failed: {json.dumps(ext_result)[:500]}")

        coordinates_data = results[0].get("data", {})
        _write_artifact(file_id, "coordinates", coordinates_data)

        _update_stage(
            file_id, "extraction", "COMPLETED",
            extractionStats={
                "lineCount": coordinates_data.get("lineCount", 0),
                "pointCount": coordinates_data.get("pointCount", 0),
            },
        )

        # ── Stage 3: Spatial Linking (integrated in extraction) ──────────
        _update_stage(file_id, "spatial-linking", "PROCESSING")

        # The spatial dimension linker is part of the extraction service
        # We pass through the coordinates as-is with linked annotations
        linked_data = coordinates_data
        _write_artifact(file_id, "coordinates", linked_data)

        _update_stage(
            file_id, "spatial-linking", "COMPLETED",
            spatialLinkingStats={
                "annotationsLinked": len(linked_data.get("annotations", [])),
            },
        )

        # ── Stage 4: Math Audit ──────────────────────────────────────────
        _update_stage(file_id, "math-audit", "PROCESSING")
        logger.info(f"[Pipeline:{file_id}] Starting dimension audit")

        # Build the audit request payload expected by the Java service
        audit_payload = {
            "scaleFactor": 0.1,  # Default scale: 1 pixel = 0.1 real-world units
            "rawCoordinates": coordinates_data,
            "tolerancePercentage": 0.5,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            audit_resp = await client.post(
                f"{DIMENSION_AUDIT_URL}/api/v1/audit/dimensions",
                json=audit_payload,
            )
            audit_resp.raise_for_status()
            audit_result = audit_resp.json()

        _write_artifact(file_id, "audit-result", audit_result)

        _update_stage(
            file_id, "math-audit", "COMPLETED",
            auditStatus=audit_result.get("status", "UNKNOWN"),
            conflictCount=len(audit_result.get("conflicts", [])),
        )

        # ── Stage 5: Compliance ──────────────────────────────────────────
        _update_stage(file_id, "compliance", "PROCESSING")
        logger.info(f"[Pipeline:{file_id}] Starting compliance check")

        compliance_payload = {
            "fileId": file_id,
            "auditResult": audit_result,
            "coordinates": coordinates_data,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                comp_resp = await client.post(
                    f"{COMPLIANCE_URL}/invoke",
                    json=compliance_payload,
                )
                comp_resp.raise_for_status()
                compliance_result = comp_resp.json()
            except Exception as comp_err:
                logger.warning(f"[Pipeline:{file_id}] Compliance check skipped/failed: {comp_err}")
                compliance_result = {
                    "status": "SKIPPED",
                    "message": f"Compliance service unavailable: {comp_err}",
                }

        _write_artifact(file_id, "compliance", compliance_result)

        _update_stage(
            file_id, "compliance", "COMPLETED",
            complianceStatus=compliance_result.get("status", "UNKNOWN"),
        )

        logger.info(f"[Pipeline:{file_id}] Full pipeline completed successfully")

    except Exception as e:
        logger.error(f"[Pipeline:{file_id}] Pipeline failed: {e}", exc_info=True)
        # Find which stage we were on
        current = read_job_status(file_id) or {}
        _update_stage(
            file_id,
            current.get("stage", "unknown"),
            "FAILED",
            error=str(e),
        )


# ---------------------------------------------------------------------------
# POST /api/v1/upload — Direct file upload from frontend
# ---------------------------------------------------------------------------
@app.post("/api/v1/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    Accept a file upload from the frontend, store it in S3,
    run format detection and ingestion, and return a fileId.

    The frontend can then connect to the SSE stream to monitor progress.
    """
    file_id = str(uuid.uuid4())
    file_name = file.filename or "unknown"
    content = await file.read()

    if len(content) < 4:
        raise HTTPException(status_code=400, detail="File too small for processing")

    logger.info(f"[Upload] Received file: {file_name}, size={len(content)} bytes, fileId={file_id}")

    # 1. Upload raw file to S3
    s3 = get_s3_client()
    raw_key = f"uploads/{file_id}/{file_name}"
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=raw_key,
        Body=content,
        Metadata={
            "original-filename": file_name,
            "file-id": file_id,
            "uploaded-via": "frontend-upload",
        },
    )
    logger.info(f"[Upload] Stored raw file at s3://{S3_BUCKET}/{raw_key}")

    # 2. Detect format via magic number
    detection = detect_format(content[:256])

    format_name = detection.format_name if detection.is_detected else "UNKNOWN"
    category = detection.category.value if detection.is_detected else "UNKNOWN"
    mime_type = detection.mime_type if detection.is_detected else "application/octet-stream"

    logger.info(
        f"[Upload] Format detected: {format_name} ({category}), mime={mime_type}"
    )

    if not detection.is_detected:
        # Write error status
        write_job_status(file_id, {
            "fileId": file_id,
            "fileName": file_name,
            "stage": "ingestion",
            "status": "FAILED",
            "error": f"Unrecognized file format. Upload: {file_name}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        raise HTTPException(
            status_code=422,
            detail={
                "fileId": file_id,
                "error": f"Unrecognized file format for '{file_name}'",
                "detectedFormat": None,
            },
        )

    # 3. Write initial job status
    write_job_status(file_id, {
        "fileId": file_id,
        "fileName": file_name,
        "detectedFormat": format_name,
        "category": category,
        "storageUri": f"s3://{S3_BUCKET}/{raw_key}",
        "stage": "ingestion",
        "status": "PROCESSING",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    # 4. Build synthetic S3 event and process through the existing Lambda handler
    # This triggers format detection, SQS routing, etc.
    try:
        synthetic_record = {
            "s3": {
                "bucket": {"name": S3_BUCKET},
                "object": {
                    "key": raw_key,
                    "size": len(content),
                },
            },
            "eventTime": datetime.now(timezone.utc).isoformat(),
        }

        result = process_s3_record(synthetic_record)

        logger.info(
            f"[Upload] Ingestion complete: status={result.get('status')}, "
            f"format={result.get('detectedFormat')}, queue={result.get('queueName')}"
        )

    # 5. Update job status to ingestion complete
        write_job_status(file_id, {
            "fileId": file_id,
            "fileName": file_name,
            "detectedFormat": format_name,
            "category": category,
            "storageUri": f"s3://{S3_BUCKET}/{raw_key}",
            "stage": "ingestion",
            "status": "COMPLETED",
            "ingestionResult": {
                "sqsMessageId": result.get("sqsMessageId"),
                "routedQueue": result.get("queueName"),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # 6. Kick off the full pipeline in the background
        routing_message = {
            "fileId": file_id,
            "fileName": file_name,
            "detectedFormat": format_name,
            "category": category,
            "mimeType": mime_type,
            "storageUri": f"s3://{S3_BUCKET}/{raw_key}",
            "fileSizeBytes": len(content),
        }
        asyncio.get_event_loop().create_task(
            _run_pipeline(file_id, category, routing_message)
        )

        return {
            "fileId": file_id,
            "fileName": file_name,
            "detectedFormat": format_name,
            "category": category,
            "mimeType": mime_type,
            "storageUri": f"s3://{S3_BUCKET}/{raw_key}",
            "status": "ACCEPTED",
            "message": f"File accepted. Connect to /api/v1/jobs/{file_id}/stream for real-time updates.",
        }

    except Exception as e:
        logger.error(f"[Upload] Ingestion processing failed: {e}", exc_info=True)

        # Update status to failed
        write_job_status(file_id, {
            "fileId": file_id,
            "fileName": file_name,
            "stage": "ingestion",
            "status": "FAILED",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        raise HTTPException(
            status_code=500,
            detail={"fileId": file_id, "error": f"Ingestion failed: {str(e)}"},
        )


# ---------------------------------------------------------------------------
# GET /api/v1/jobs/{fileId}/stream — SSE endpoint
# ---------------------------------------------------------------------------
@app.get("/api/v1/jobs/{file_id}/stream")
async def job_stream(file_id: str):
    """
    Server-Sent Events (SSE) endpoint for real-time pipeline status.

    The server monitors S3 for processing artifacts and pushes events
    to the client as they become available:

    Events:
      - stage-update:   A pipeline stage changed status
      - coordinates:    Raw coordinate data is available (for canvas rendering)
      - audit-result:   Dimension audit is complete (for triage mode)
      - compliance:     Compliance report is available
      - done:           Pipeline completed successfully
      - error:          Pipeline encountered an error
    """

    async def event_generator():
        """Async generator that yields SSE events by monitoring S3."""
        sent_stages = set()
        sent_artifacts = set()
        stage_index = {s: i for i, s in enumerate(PIPELINE_STAGES)}
        max_iterations = 300  # 5 minutes at 1s interval
        iteration = 0
        pipeline_done = False

        logger.info(f"[SSE] Client connected for fileId={file_id}")

        # Yield initial connection event
        yield _sse_event("connected", {"fileId": file_id, "message": "Stream connected"})

        while iteration < max_iterations and not pipeline_done:
            iteration += 1

            try:
                status = read_job_status(file_id)
            except Exception as e:
                logger.warning(f"[SSE] Failed to read status: {e}")
                yield _sse_event("heartbeat", {"iteration": iteration})
                await asyncio.sleep(1)
                continue

            if status is None:
                # Job not found yet — might still be writing
                yield _sse_event("heartbeat", {"iteration": iteration})
                await asyncio.sleep(1)
                continue

            current_stage = status.get("stage", "unknown")
            current_status = status.get("status", "UNKNOWN")
            stage_key = f"{current_stage}:{current_status}"

            # --- Push stage update if new ---
            if stage_key not in sent_stages:
                sent_stages.add(stage_key)

                yield _sse_event("stage-update", {
                    "fileId": file_id,
                    "stage": current_stage,
                    "status": current_status,
                    "timestamp": status.get("timestamp", datetime.now(timezone.utc).isoformat()),
                    "fileName": status.get("fileName"),
                    "detectedFormat": status.get("detectedFormat"),
                })

            # --- Check for error state ---
            if current_status == "FAILED":
                yield _sse_event("error", {
                    "fileId": file_id,
                    "stage": current_stage,
                    "message": status.get("error", "Processing failed"),
                    "timestamp": status.get("timestamp"),
                })
                pipeline_done = True
                break

            # --- Check for coordinate artifacts ---
            if "coordinates" not in sent_artifacts:
                coords_data = read_job_artifact(file_id, "coordinates")
                if coords_data is not None:
                    sent_artifacts.add("coordinates")
                    yield _sse_event("coordinates", {
                        "fileId": file_id,
                        "data": coords_data,
                    })

            # --- Check for audit result artifacts ---
            if "audit-result" not in sent_artifacts:
                audit_data = read_job_artifact(file_id, "audit-result")
                if audit_data is not None:
                    sent_artifacts.add("audit-result")
                    yield _sse_event("audit-result", {
                        "fileId": file_id,
                        "data": audit_data,
                    })

            # --- Check for compliance artifacts ---
            if "compliance" not in sent_artifacts:
                compliance_data = read_job_artifact(file_id, "compliance")
                if compliance_data is not None:
                    sent_artifacts.add("compliance")
                    yield _sse_event("compliance", {
                        "fileId": file_id,
                        "data": compliance_data,
                    })

            # --- Check if pipeline is fully done ---
            if current_status == "COMPLETED" and current_stage == "compliance":
                yield _sse_event("done", {
                    "fileId": file_id,
                    "status": "COMPLETED",
                    "timestamp": status.get("timestamp"),
                })
                pipeline_done = True
                break

            # --- Heartbeat every 5 iterations ---
            if iteration % 5 == 0:
                yield _sse_event("heartbeat", {"iteration": iteration})

            await asyncio.sleep(1)

        if not pipeline_done:
            yield _sse_event("error", {
                "fileId": file_id,
                "message": "Stream timeout — pipeline did not complete within 5 minutes",
            })

        logger.info(f"[SSE] Stream closed for fileId={file_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


# ---------------------------------------------------------------------------
# GET /api/v1/jobs/{fileId} — Synchronous status check (fallback)
# ---------------------------------------------------------------------------
@app.get("/api/v1/jobs/{file_id}")
async def get_job_status(file_id: str):
    """Return current job status synchronously."""
    status = read_job_status(file_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Job {file_id} not found")
    return status


# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------
def _sse_event(event: str, data: dict) -> str:
    """Format a server-sent event."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# ---------------------------------------------------------------------------
# Legacy Lambda endpoints (unchanged)
# ---------------------------------------------------------------------------
class S3EventPayload:
    """Placeholder for S3 event payload."""
    pass


@app.post("/")
@app.post("/invoke")
def invoke_lambda(event: dict):
    """Invoke the Lambda handler with an S3 event payload."""
    from lambda_function import lambda_handler
    result = lambda_handler(event, None)
    return result


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "file-ingestion", "version": "2.0.0"}


if __name__ == "__main__":
    port = int(os.environ.get("SERVICE_PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)