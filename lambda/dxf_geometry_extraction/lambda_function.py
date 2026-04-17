"""
AWS Lambda Handler — DXF Geometry Extraction Microservice

Triggered by the Vector-Processing-Queue (SQS). Downloads a DXF file from S3,
parses it with ezdxf (read-only, no rendering), and extracts every LINE,
LWPOLYLINE, and MTEXT entity into a standardized JSON output conforming to
the raw-coordinates.schema.json data contract.

Pipeline per SQS record:
  1. Parse SQS message → extract S3 URI + file metadata.
  2. Download DXF from S3 to Lambda /tmp storage.
  3. Parse with ezdxf.readfile() (pure geometry — no visual rendering).
  4. Extract LINE, LWPOLYLINE, MTEXT entities → edges, points, annotations.
  5. Deduplicate points, build line references, parse dimension text.
  6. Output strictly typed JSON matching raw-coordinates.schema.json.

Environment Variables:
  COORDINATE_PRECISION  — Decimal places for point deduplication (default: 6)
  MIN_EDGE_LENGTH       — Minimum edge length to include (default: 1e-9)
  MAX_FILE_SIZE_MB      — Maximum DXF file size in MB (default: 100)
  COORDINATE_UNITS      — Drawing units: millimeters|centimeters|meters|inches|feet
  LOG_LEVEL             — Logging verbosity (default: INFO)

IAM Permissions Required:
  - s3:GetObject (on source bucket)
"""

import json
import logging
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from exceptions import DXFParseError, EmptyDXFError, InvalidInputError, S3DownloadError
from geometry_extractor import extract_geometry, extraction_result_to_schema

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
MAX_FILE_SIZE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", "100"))
COORDINATE_UNITS = os.environ.get("COORDINATE_UNITS", "meters")

# S3 download chunk size (8 MB)
S3_CHUNK_SIZE = 8 * 1024 * 1024

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


logger = logging.getLogger("dxf_geometry_extraction")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)
logger.propagate = False


def _log_extra(**kwargs: Any) -> dict[str, Any]:
    """Helper to create structured logging extra fields."""
    return {"extra_fields": kwargs}


# ---------------------------------------------------------------------------
# AWS Client Cache (initialized once per Lambda container lifecycle)
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
    """Parse an S3 URI into bucket and key components.

    Args:
        s3_uri: S3 URI in the format 's3://bucket-name/path/to/object'

    Returns:
        Tuple of (bucket, key).

    Raises:
        InvalidInputError: If the URI format is invalid.
    """
    if not s3_uri.startswith("s3://"):
        raise InvalidInputError(
            message=f"Invalid S3 URI scheme: '{s3_uri}'",
            missing_fields=["storageUri"],
        )

    # Remove 's3://' prefix and split
    path = s3_uri[5:]
    slash_idx = path.find("/")

    if slash_idx == -1 or slash_idx == len(path) - 1:
        raise InvalidInputError(
            message=f"S3 URI missing object key: '{s3_uri}'",
            missing_fields=["storageUri"],
        )

    bucket = path[:slash_idx]
    key = path[slash_idx + 1:]

    return bucket, key


def download_dxf_from_s3(bucket: str, key: str) -> str:
    """Download a DXF file from S3 to Lambda /tmp storage.

    Streams the file in chunks with a size check to prevent memory
    exhaustion from unexpectedly large files.

    Args:
        bucket: S3 bucket name.
        key: S3 object key.

    Returns:
        Local file path of the downloaded DXF file.

    Raises:
        S3DownloadError: If the download fails.
    """
    s3 = get_s3_client()

    # Extract filename from key for the temp file
    filename = key.split("/")[-1] if "/" in key else key
    suffix = Path(filename).suffix or ".dxf"

    try:
        # First, check the object size via HEAD request
        head = s3.head_object(Bucket=bucket, Key=key)
        content_length = head.get("ContentLength", 0)
        max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024

        if content_length > max_bytes:
            raise S3DownloadError(
                message=(
                    f"DXF file exceeds maximum allowed size: "
                    f"{content_length / (1024 * 1024):.1f} MB > {MAX_FILE_SIZE_MB} MB"
                ),
                bucket=bucket,
                key=key,
            )

        logger.info(
            "Starting S3 download",
            extra=_log_extra(
                bucket=bucket,
                key=key,
                content_length_mb=round(content_length / (1024 * 1024), 2),
            ),
        )

        # Create a temp file and stream the object into it
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="dxf_")
        try:
            response = s3.get_object(Bucket=bucket, Key=key)
            stream = response["Body"]
            bytes_written = 0

            with os.fdopen(tmp_fd, "wb") as f:
                while True:
                    chunk = stream.read(S3_CHUNK_SIZE)
                    if not chunk:
                        break
                    bytes_written += len(chunk)

                    # Guard against oversized files during streaming
                    if bytes_written > max_bytes:
                        raise S3DownloadError(
                            message=(
                                f"DXF file exceeded size limit during download: "
                                f"{bytes_written / (1024 * 1024):.1f} MB > {MAX_FILE_SIZE_MB} MB"
                            ),
                            bucket=bucket,
                            key=key,
                        )

                    f.write(chunk)

        except S3DownloadError:
            # Clean up the temp file on size violation
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        logger.info(
            "DXF file downloaded successfully",
            extra=_log_extra(
                bucket=bucket,
                key=key,
                tmp_path=tmp_path,
                bytes_downloaded=bytes_written,
            ),
        )
        return tmp_path

    except S3DownloadError:
        raise
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "Unknown")
        logger.error(
            "Failed to download DXF from S3",
            extra=_log_extra(
                bucket=bucket,
                key=key,
                error_code=error_code,
                error_message=str(exc),
            ),
        )
        raise S3DownloadError(
            message=f"Cannot download s3://{bucket}/{key}: {exc}",
            bucket=bucket,
            key=key,
        ) from exc


def cleanup_temp_file(tmp_path: str) -> None:
    """Safely delete a temporary file, ignoring errors if it doesn't exist."""
    try:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
            logger.debug(f"Cleaned up temp file: {tmp_path}")
    except OSError as exc:
        logger.warning(
            f"Failed to clean up temp file: {tmp_path}",
            extra=_log_extra(error=str(exc)),
        )


# ---------------------------------------------------------------------------
# SQS Message Parsing
# ---------------------------------------------------------------------------


def parse_sqs_message(record: dict[str, Any]) -> dict[str, Any]:
    """Parse an SQS record to extract the file metadata payload.

    The message body should contain the routing message produced by
    the file_ingestion Lambda, which includes:
      - storageUri: S3 URI of the DXF file
      - fileId: UUID of the uploaded file
      - fileName: Original filename
      - mimeType: Should be "application/dxf"

    Args:
        record: A single SQS record from the event.

    Returns:
        Parsed message body as a dict.

    Raises:
        InvalidInputError: If required fields are missing.
    """
    try:
        body = record.get("body", "{}")
        if isinstance(body, str):
            message = json.loads(body)
        else:
            message = body
    except json.JSONDecodeError as exc:
        raise InvalidInputError(
            message=f"Invalid JSON in SQS message body: {exc}",
            missing_fields=["body"],
        ) from exc

    # Validate required fields
    missing = []
    if not message.get("storageUri"):
        missing.append("storageUri")
    if not message.get("fileId"):
        missing.append("fileId")

    if missing:
        raise InvalidInputError(
            message=f"SQS message missing required fields: {missing}",
            missing_fields=missing,
        )

    return message


# ---------------------------------------------------------------------------
# Core Processing — Per-Record Handler
# ---------------------------------------------------------------------------


def process_sqs_record(record: dict[str, Any], request_id: str) -> dict[str, Any]:
    """Process a single SQS record through the full extraction pipeline.

    Pipeline stages:
      1. Parse SQS message → extract S3 URI + metadata.
      2. Download DXF from S3 to /tmp.
      3. Extract geometry (LINE, LWPOLYLINE, MTEXT).
      4. Convert to schema-compliant JSON output.
      5. Clean up temp file.

    Args:
        record: A single SQS record from the event.
        request_id: Lambda request ID for log correlation.

    Returns:
        Dict with extraction result conforming to raw-coordinates.schema.json.

    Raises:
        DXFParseError: If the DXF file cannot be parsed.
        EmptyDXFError: If the DXF contains no geometric entities.
        S3DownloadError: If the file cannot be downloaded from S3.
        InvalidInputError: If the input message is malformed.
    """
    # ── Stage 1: Parse SQS message ───────────────────────────────────────
    message = parse_sqs_message(record)
    storage_uri = message["storageUri"]
    file_id = message["fileId"]
    file_name = message.get("fileName", "unknown.dxf")

    logger.info(
        "Processing DXF extraction request",
        extra=_log_extra(
            request_id=request_id,
            file_id=file_id,
            file_name=file_name,
            storage_uri=storage_uri,
        ),
    )

    # Parse S3 URI
    bucket, key = parse_s3_uri(storage_uri)

    # ── Stage 2: Download DXF from S3 ────────────────────────────────────
    tmp_path = None
    try:
        tmp_path = download_dxf_from_s3(bucket, key)

        # ── Stage 3: Extract geometry ────────────────────────────────────
        result = extract_geometry(tmp_path)

        # ── Stage 4: Convert to schema-compliant output ──────────────────
        output = extraction_result_to_schema(
            result=result,
            source_file_id=file_id,
            source_zones_id=message.get("zonesId"),
            coordinate_units=COORDINATE_UNITS,
        )

        logger.info(
            "DXF geometry extraction completed successfully",
            extra=_log_extra(
                request_id=request_id,
                file_id=file_id,
                file_name=file_name,
                unique_points=len(result.points),
                total_edges=len(result.edges),
                total_annotations=len(result.annotations),
                entity_counts=result.entity_counts,
            ),
        )

        return output

    finally:
        # ── Stage 5: Cleanup ─────────────────────────────────────────────
        if tmp_path:
            cleanup_temp_file(tmp_path)


# ---------------------------------------------------------------------------
# Lambda Entry Point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda entry point for Vector-Processing-Queue (SQS) processing.

    Handles batched SQS records, processing each independently.
    Per-record errors are captured without failing the entire batch.

    Args:
        event: The SQS event payload containing one or more records.
        context: Lambda runtime context (function name, request ID, etc.).

    Returns:
        Summary dict with per-record results and overall status.
    """
    request_id = getattr(context, "aws_request_id", "local")
    function_name = getattr(context, "function_name", "dxf-geometry-extraction")

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
        logger.warning(
            "Lambda invoked with no SQS records",
            extra=_log_extra(request_id=request_id),
        )
        return {"status": "NO_RECORDS", "results": [], "errorCount": 0}

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for idx, record in enumerate(records):
        record_id = f"{request_id}#{idx}"
        try:
            result = process_sqs_record(record, request_id)
            results.append({
                "recordId": record_id,
                "status": "EXTRACTED",
                "coordinatesId": result["coordinatesId"],
                "pointCount": len(result["points"]),
                "lineCount": len(result["lines"]),
                "annotationCount": len(result["dimensionAnnotations"]),
                "edgeCount": len(result.get("edges", [])),
                "data": result,
            })

        except DXFParseError as exc:
            error_detail = {
                "recordId": record_id,
                "status": "DXF_PARSE_ERROR",
                "error": exc.to_dict(),
            }
            errors.append(error_detail)
            results.append(error_detail)
            logger.error(
                "DXF parse error during processing",
                extra=_log_extra(
                    record_id=record_id,
                    error=str(exc),
                    file_key=exc.file_key,
                ),
            )

        except EmptyDXFError as exc:
            error_detail = {
                "recordId": record_id,
                "status": "EMPTY_DXF_ERROR",
                "error": exc.to_dict(),
            }
            errors.append(error_detail)
            results.append(error_detail)
            logger.warning(
                "DXF file contained no geometric entities",
                extra=_log_extra(
                    record_id=record_id,
                    error=str(exc),
                    entity_counts=exc.entity_counts,
                ),
            )

        except S3DownloadError as exc:
            error_detail = {
                "recordId": record_id,
                "status": "S3_DOWNLOAD_ERROR",
                "error": exc.to_dict(),
            }
            errors.append(error_detail)
            results.append(error_detail)
            logger.error(
                "S3 download failure during processing",
                extra=_log_extra(
                    record_id=record_id,
                    error=str(exc),
                    bucket=exc.bucket,
                    key=exc.key,
                ),
            )

        except InvalidInputError as exc:
            error_detail = {
                "recordId": record_id,
                "status": "INVALID_INPUT_ERROR",
                "error": exc.to_dict(),
            }
            errors.append(error_detail)
            results.append(error_detail)
            logger.error(
                "Invalid input in SQS message",
                extra=_log_extra(
                    record_id=record_id,
                    error=str(exc),
                    missing_fields=exc.missing_fields,
                ),
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
                "Unexpected error during record processing",
                extra=_log_extra(record_id=record_id, error=str(exc)),
                exc_info=True,
            )

    # ── Build final summary ──────────────────────────────────────────────
    extracted_count = sum(1 for r in results if r.get("status") == "EXTRACTED")
    error_count = len(errors)

    summary = {
        "status": "COMPLETED_WITH_ERRORS" if error_count > 0 else "COMPLETED",
        "totalRecords": len(records),
        "extractedCount": extracted_count,
        "errorCount": error_count,
        "results": results,
        "requestId": request_id,
    }

    logger.info(
        "Lambda invocation completed",
        extra=_log_extra(
            request_id=request_id,
            total_records=len(records),
            extracted=extracted_count,
            errors=error_count,
            status=summary["status"],
        ),
    )

    return summary