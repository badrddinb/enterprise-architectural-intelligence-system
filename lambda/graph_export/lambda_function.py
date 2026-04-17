"""
AWS Lambda Handler — Graph Export Microservice

Triggered by the Export-Queue (SQS). Downloads a CertifiedMathGraph JSON,
validates it, and generates both IFC and GeoJSON export files.

Pipeline per SQS record:
  1. Parse SQS message → extract graph payload or S3 URI.
  2. Load & validate graph against certified-math-graph.schema.json.
  3. Export IFC4 file via IfcOpenShell (3.0 m wall extrusion).
  4. Export GeoJSON FeatureCollection (LineString walls + Polygon faces).
  5. Upload both files to the destination S3 bucket.
  6. Return structured summary.

Environment Variables:
  EXTRUSION_HEIGHT      — Wall Z-extrusion in metres (default: 3.0)
  DEFAULT_WALL_THICKNESS — Fallback wall thickness in metres (default: 0.20)
  OUTPUT_BUCKET          — S3 bucket for exported files
  OUTPUT_PREFIX          — S3 key prefix (default: exports/)
  LOG_LEVEL              — Logging verbosity (default: INFO)
  GRAPH_SCHEMA_PATH      — Override path to certified-math-graph.schema.json

IAM Permissions Required:
  - s3:GetObject (on source bucket)
  - s3:PutObject (on output bucket)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from exceptions import (
    GeoJSONExportError,
    GraphExportError,
    IFCExportError,
    InvalidGraphError,
)
from geojson_exporter import export_geojson
from graph_loader import GraphData, load_graph
from ifc_exporter import export_ifc

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
EXTRUSION_HEIGHT = float(os.environ.get("EXTRUSION_HEIGHT", "3.0"))
DEFAULT_WALL_THICKNESS = float(os.environ.get("DEFAULT_WALL_THICKNESS", "0.20"))
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "")
OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", "exports/")

# ---------------------------------------------------------------------------
# Structured Logging Setup
# ---------------------------------------------------------------------------


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for CloudWatch integration."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "Unknown",
                "message": str(record.exc_info[1]),
            }
        return json.dumps(log_entry, default=str)


logger = logging.getLogger("graph_export")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)
logger.propagate = False


def _log_extra(**kwargs: Any) -> dict[str, Any]:
    """Helper to create structured logging extra fields."""
    return {"extra_fields": kwargs}


# ---------------------------------------------------------------------------
# AWS Client Cache
# ---------------------------------------------------------------------------
_s3_client: Optional[Any] = None


def get_s3_client() -> Any:
    """Lazy-initialize and return a cached S3 client."""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


# ---------------------------------------------------------------------------
# S3 Operations
# ---------------------------------------------------------------------------


def parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    """Parse an S3 URI into bucket and key components."""
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI scheme: '{s3_uri}'")
    path = s3_uri[5:]
    slash_idx = path.find("/")
    if slash_idx == -1:
        raise ValueError(f"S3 URI missing object key: '{s3_uri}'")
    return path[:slash_idx], path[slash_idx + 1:]


def download_json_from_s3(bucket: str, key: str) -> dict[str, Any]:
    """Download and parse a JSON file from S3."""
    s3 = get_s3_client()
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read().decode("utf-8")
        return json.loads(body)
    except (ClientError, json.JSONDecodeError) as exc:
        raise GraphExportError(
            f"Cannot download/parse s3://{bucket}/{key}: {exc}",
            bucket=bucket,
            key=key,
        ) from exc


def upload_file_to_s3(local_path: str | Path, bucket: str, key: str) -> str:
    """Upload a local file to S3, returning the S3 URI."""
    s3 = get_s3_client()
    try:
        s3.upload_file(str(local_path), bucket, key)
        uri = f"s3://{bucket}/{key}"
        logger.info("Uploaded %s", uri, extra=_log_extra(bucket=bucket, key=key))
        return uri
    except ClientError as exc:
        raise GraphExportError(
            f"Cannot upload to s3://{bucket}/{key}: {exc}",
            bucket=bucket,
            key=key,
        ) from exc


# ---------------------------------------------------------------------------
# SQS Message Parsing
# ---------------------------------------------------------------------------


def parse_sqs_message(record: dict[str, Any]) -> dict[str, Any]:
    """Parse an SQS record to extract the graph export payload.

    Expected fields:
      - graphUri: S3 URI of the CertifiedMathGraph JSON
      - graphId: UUID of the graph
      - formats: ["ifc", "geojson"] (optional, defaults to both)
    """
    try:
        body = record.get("body", "{}")
        if isinstance(body, str):
            message = json.loads(body)
        else:
            message = body
    except json.JSONDecodeError as exc:
        raise GraphExportError(
            f"Invalid JSON in SQS message body: {exc}",
        ) from exc

    if not message.get("graphUri"):
        raise GraphExportError("SQS message missing required field: graphUri")

    return message


# ---------------------------------------------------------------------------
# Core Processing
# ---------------------------------------------------------------------------


def process_export(record: dict[str, Any], request_id: str) -> dict[str, Any]:
    """Process a single export request through the full pipeline.

    Pipeline:
      1. Parse SQS message → extract graph URI + formats.
      2. Download graph JSON from S3.
      3. Load & validate graph.
      4. Export to requested formats (IFC, GeoJSON, or both).
      5. Upload results to output bucket.
      6. Return summary.
    """
    # ── Stage 1: Parse message ────────────────────────────────────────────
    message = parse_sqs_message(record)
    graph_uri = message["graphUri"]
    graph_id = message.get("graphId", "unknown")
    formats = message.get("formats", ["ifc", "geojson"])

    logger.info(
        "Processing export request",
        extra=_log_extra(
            request_id=request_id,
            graph_id=graph_id,
            graph_uri=graph_uri,
            formats=formats,
        ),
    )

    # ── Stage 2: Download graph ───────────────────────────────────────────
    bucket, key = parse_s3_uri(graph_uri)
    graph_json = download_json_from_s3(bucket, key)

    # ── Stage 3: Load & validate ──────────────────────────────────────────
    graph = load_graph(graph_json)

    results: dict[str, Any] = {
        "graphId": graph.graph_id,
        "nodeCount": graph.node_count,
        "edgeCount": graph.edge_count,
        "wallCount": graph.wall_count,
        "faceCount": graph.face_count,
        "exports": {},
    }

    tmp_files: list[str] = []

    try:
        # ── Stage 4a: IFC Export ──────────────────────────────────────────
        if "ifc" in formats:
            ifc_tmp = tempfile.mktemp(suffix=".ifc", prefix="graph_export_")
            ifc_path = export_ifc(
                graph,
                ifc_tmp,
                extrusion_height=EXTRUSION_HEIGHT,
            )
            tmp_files.append(str(ifc_path))

            # Upload to S3
            if OUTPUT_BUCKET:
                ifc_key = f"{OUTPUT_PREFIX}{graph.graph_id}/{graph.graph_id}.ifc"
                ifc_uri = upload_file_to_s3(ifc_path, OUTPUT_BUCKET, ifc_key)
                results["exports"]["ifc"] = {
                    "uri": ifc_uri,
                    "wallCount": graph.wall_count,
                    "extrusionHeight": EXTRUSION_HEIGHT,
                }
            else:
                results["exports"]["ifc"] = {
                    "localPath": str(ifc_path),
                    "wallCount": graph.wall_count,
                    "extrusionHeight": EXTRUSION_HEIGHT,
                }

        # ── Stage 4b: GeoJSON Export ─────────────────────────────────────
        if "geojson" in formats:
            geojson_tmp = tempfile.mktemp(suffix=".geojson", prefix="graph_export_")
            geojson_path = Path(geojson_tmp)
            geojson_data = export_geojson(graph, geojson_path)
            tmp_files.append(str(geojson_path))

            if OUTPUT_BUCKET:
                gj_key = f"{OUTPUT_PREFIX}{graph.graph_id}/{graph.graph_id}.geojson"
                gj_uri = upload_file_to_s3(geojson_path, OUTPUT_BUCKET, gj_key)
                results["exports"]["geojson"] = {
                    "uri": gj_uri,
                    "featureCount": len(geojson_data["features"]),
                }
            else:
                results["exports"]["geojson"] = {
                    "localPath": str(geojson_path),
                    "featureCount": len(geojson_data["features"]),
                }

        results["status"] = "EXPORTED"
        return results

    finally:
        # ── Stage 5: Cleanup temp files ──────────────────────────────────
        for tmp in tmp_files:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Lambda Entry Point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda entry point for graph export processing.

    Handles batched SQS records, processing each independently.

    Args:
        event:   SQS event payload containing one or more records.
        context: Lambda runtime context.

    Returns:
        Summary dict with per-record results and overall status.
    """
    request_id = getattr(context, "aws_request_id", "local")
    function_name = getattr(context, "function_name", "graph-export")

    logger.info(
        "Lambda invocation started",
        extra=_log_extra(
            request_id=request_id,
            function_name=function_name,
            record_count=len(event.get("Records", [])),
        ),
    )

    records = event.get("Records", [])
    if not records:
        return {"status": "NO_RECORDS", "results": [], "errorCount": 0}

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for idx, record in enumerate(records):
        record_id = f"{request_id}#{idx}"
        try:
            result = process_export(record, request_id)
            results.append({
                "recordId": record_id,
                "status": "EXPORTED",
                **result,
            })

        except InvalidGraphError as exc:
            error_detail = {
                "recordId": record_id,
                "status": "INVALID_GRAPH_ERROR",
                "error": exc.to_dict(),
            }
            errors.append(error_detail)
            results.append(error_detail)
            logger.error(
                "Invalid graph during export",
                extra=_log_extra(record_id=record_id, error=str(exc)),
            )

        except IFCExportError as exc:
            error_detail = {
                "recordId": record_id,
                "status": "IFC_EXPORT_ERROR",
                "error": exc.to_dict(),
            }
            errors.append(error_detail)
            results.append(error_detail)
            logger.error(
                "IFC export failed",
                extra=_log_extra(record_id=record_id, error=str(exc)),
            )

        except GeoJSONExportError as exc:
            error_detail = {
                "recordId": record_id,
                "status": "GEOJSON_EXPORT_ERROR",
                "error": exc.to_dict(),
            }
            errors.append(error_detail)
            results.append(error_detail)
            logger.error(
                "GeoJSON export failed",
                extra=_log_extra(record_id=record_id, error=str(exc)),
            )

        except GraphExportError as exc:
            error_detail = {
                "recordId": record_id,
                "status": "EXPORT_ERROR",
                "error": exc.to_dict(),
            }
            errors.append(error_detail)
            results.append(error_detail)
            logger.error(
                "Export error",
                extra=_log_extra(record_id=record_id, error=str(exc)),
            )

        except Exception as exc:
            error_detail = {
                "recordId": record_id,
                "status": "UNEXPECTED_ERROR",
                "error": {
                    "errorType": type(exc).__name__,
                    "errorMessage": str(exc),
                },
            }
            errors.append(error_detail)
            results.append(error_detail)
            logger.error(
                "Unexpected error during export",
                extra=_log_extra(record_id=record_id, error=str(exc)),
                exc_info=True,
            )

    # ── Build final summary ──────────────────────────────────────────────
    exported_count = sum(1 for r in results if r.get("status") == "EXPORTED")
    error_count = len(errors)

    summary = {
        "status": "COMPLETED_WITH_ERRORS" if error_count > 0 else "COMPLETED",
        "totalRecords": len(records),
        "exportedCount": exported_count,
        "errorCount": error_count,
        "results": results,
        "requestId": request_id,
    }

    logger.info(
        "Lambda invocation completed",
        extra=_log_extra(
            request_id=request_id,
            total_records=len(records),
            exported=exported_count,
            errors=error_count,
            status=summary["status"],
        ),
    )

    return summary