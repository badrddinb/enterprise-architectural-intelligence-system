"""
AWS Lambda Handler — Secure File Ingestion & Format Routing

Triggered by S3 Event Notifications. Reads the first bytes of each uploaded
file to determine its format via magic number detection (never file extensions),
then routes metadata to the appropriate SQS processing queue:

  RASTER formats (PDF, PNG, TIFF) → Raster-Processing-Queue
  VECTOR formats (DWG, DXF, IFC)  → Vector-Processing-Queue
  UNRECOGNIZED                    → FatalFormatError → Format-DLQ

Environment Variables:
  RASTER_QUEUE_NAME  — SQS queue for raster files (default: Raster-Processing-Queue)
  VECTOR_QUEUE_NAME  — SQS queue for vector files (default: Vector-Processing-Queue)
  DLQ_QUEUE_NAME     — SQS dead letter queue (default: Format-DLQ)
  DLQ_BUCKET_NAME    — S3 bucket for storing unrecognized files (optional)
  LOG_LEVEL          — Logging verbosity (default: INFO)

IAM Permissions Required:
  - s3:GetObject (on source bucket)
  - s3:HeadObject (on source bucket)
  - sqs:GetQueueUrl
  - sqs:SendMessage
  - s3:PutObject (on DLQ bucket, if configured)
"""

import hashlib
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from exceptions import FatalFormatError, FileTooSmallError, QueueRoutingError, S3AccessError
from magic_numbers import (
    DETECTION_WINDOW_BYTES,
    FormatCategory,
    detect_format,
    format_header_hex,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
RASTER_QUEUE_NAME = os.environ.get("RASTER_QUEUE_NAME", "Raster-Processing-Queue")
VECTOR_QUEUE_NAME = os.environ.get("VECTOR_QUEUE_NAME", "Vector-Processing-Queue")
DLQ_QUEUE_NAME = os.environ.get("DLQ_QUEUE_NAME", "Format-DLQ")
DLQ_BUCKET_NAME = os.environ.get("DLQ_BUCKET_NAME", "")

# SHA-256 computation: chunk size for streaming (8 MB)
SHA256_CHUNK_SIZE = 8 * 1024 * 1024

# ---------------------------------------------------------------------------
# Structured Logging Setup
# --------------------------------------------------------------------------


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for CloudWatch integration."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        # Merge any extra fields passed via logging extras
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "Unknown",
                "message": str(record.exc_info[1]),
            }
        return json.dumps(log_entry, default=str)


logger = logging.getLogger("file_ingestion")
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
_sqs_client: Optional[Any] = None
_queue_url_cache: dict[str, str] = {}


def get_s3_client() -> Any:
    """Lazy-initialize and return a cached S3 client.

    Uses ``request_checksum_calculation="when_required"`` to prevent
    boto3 >= 1.42 from auto-computing CRC-32 checksums on every PutObject
    call.  LocalStack (and some S3-compatible stores) compute a different
    checksum, which causes the dreaded
    ``Expected checksum X did not match calculated checksum Y`` error.
    """
    global _s3_client
    if _s3_client is None:
        s3_config = Config(
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        _s3_client = boto3.client("s3", config=s3_config)
    return _s3_client


def get_sqs_client() -> Any:
    """Lazy-initialize and return a cached SQS client."""
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = boto3.client("sqs")
    return _sqs_client


def get_queue_url(queue_name: str) -> str:
    """Resolve and cache an SQS queue URL by name.

    Args:
        queue_name: The SQS queue name (not the URL).

    Returns:
        The full queue URL string.

    Raises:
        QueueRoutingError: If the queue cannot be found.
    """
    if queue_name in _queue_url_cache:
        return _queue_url_cache[queue_name]

    sqs = get_sqs_client()
    try:
        response = sqs.get_queue_url(QueueName=queue_name)
        url = response["QueueUrl"]
        _queue_url_cache[queue_name] = url
        logger.info(
            "Resolved SQS queue URL",
            extra=_log_extra(queue_name=queue_name, queue_url=url),
        )
        return url
    except ClientError as exc:
        raise QueueRoutingError(
            f"Failed to resolve SQS queue '{queue_name}': {exc}",
            queue_name=queue_name,
        ) from exc


# ---------------------------------------------------------------------------
# S3 Operations
# --------------------------------------------------------------------------


def read_file_header(bucket: str, key: str, size: int = DETECTION_WINDOW_BYTES) -> bytes:
    """Read the first N bytes of an S3 object using a Range GET request.

    This avoids downloading the entire file — only the header bytes needed
    for magic number detection are transferred.

    Args:
        bucket: S3 bucket name.
        key: S3 object key.
        size: Number of bytes to read (default: DETECTION_WINDOW_BYTES).

    Returns:
        The raw bytes from the start of the file.

    Raises:
        S3AccessError: If the object cannot be read.
    """
    s3 = get_s3_client()
    try:
        response = s3.get_object(
            Bucket=bucket,
            Key=key,
            Range=f"bytes=0-{size - 1}",
        )
        header_bytes = response["Body"].read()
        logger.debug(
            "Read file header from S3",
            extra=_log_extra(
                bucket=bucket,
                key=key,
                bytes_read=len(header_bytes),
                requested=size,
            ),
        )
        return header_bytes
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "Unknown")
        logger.error(
            "Failed to read file header from S3",
            extra=_log_extra(
                bucket=bucket,
                key=key,
                error_code=error_code,
                error_message=str(exc),
            ),
        )
        raise S3AccessError(
            f"Cannot read s3://{bucket}/{key}: {exc}",
            bucket=bucket,
            key=key,
        ) from exc


def get_object_metadata(bucket: str, key: str) -> dict[str, Any]:
    """Retrieve object metadata via HEAD request (no body download).

    Returns:
        Dict with 'content_length', 'last_modified', 'etag', 'content_type',
        'user_metadata' keys.
    """
    s3 = get_s3_client()
    try:
        response = s3.head_object(Bucket=bucket, Key=key)
        return {
            "content_length": response.get("ContentLength", 0),
            "last_modified": response.get("LastModified"),
            "etag": response.get("ETag", "").strip('"'),
            "content_type": response.get("ContentType", "application/octet-stream"),
            "user_metadata": response.get("Metadata", {}),
            "checksum_sha256": response.get("ChecksumSHA256"),
        }
    except ClientError as exc:
        logger.warning(
            "Failed to get object metadata, using defaults",
            extra=_log_extra(
                bucket=bucket,
                key=key,
                error_message=str(exc),
            ),
        )
        return {
            "content_length": 0,
            "last_modified": None,
            "etag": "unknown",
            "content_type": "application/octet-stream",
            "user_metadata": {},
            "checksum_sha256": None,
        }


def compute_sha256(bucket: str, key: str) -> str:
    """Compute SHA-256 checksum of an S3 object by streaming it in chunks.

    For large files this is I/O intensive. If the object was uploaded with
    an S3 ChecksumSHA256, that value is preferred (retrieved via head_object).

    Args:
        bucket: S3 bucket name.
        key: S3 object key.

    Returns:
        Lowercase hex digest of the SHA-256 hash.
    """
    # First, try to get pre-computed checksum from S3 metadata
    meta = get_object_metadata(bucket, key)
    if meta.get("checksum_sha256"):
        logger.info(
            "Using pre-computed SHA-256 from S3 object metadata",
            extra=_log_extra(bucket=bucket, key=key),
        )
        return meta["checksum_sha256"].lower()

    # Fallback: stream the object and compute SHA-256
    s3 = get_s3_client()
    sha256 = hashlib.sha256()
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        stream = response["Body"]
        bytes_hashed = 0
        while True:
            chunk = stream.read(SHA256_CHUNK_SIZE)
            if not chunk:
                break
            sha256.update(chunk)
            bytes_hashed += len(chunk)
        digest = sha256.hexdigest()
        logger.info(
            "Computed SHA-256 via streaming",
            extra=_log_extra(
                bucket=bucket,
                key=key,
                bytes_hashed=bytes_hashed,
                sha256_digest=digest[:16] + "...",
            ),
        )
        return digest
    except ClientError as exc:
        logger.error(
            "Failed to compute SHA-256",
            extra=_log_extra(bucket=bucket, key=key, error_message=str(exc)),
        )
        raise S3AccessError(
            f"Cannot compute SHA-256 for s3://{bucket}/{key}: {exc}",
            bucket=bucket,
            key=key,
        ) from exc


def move_to_dlq_bucket(source_bucket: str, source_key: str, reason: str) -> Optional[str]:
    """Copy an unrecognized file to the DLQ S3 bucket for later analysis.

    Args:
        source_bucket: Original S3 bucket.
        source_key: Original S3 object key.
        reason: Reason for the DLQ move.

    Returns:
        The S3 URI of the copied file, or None if DLQ bucket is not configured.
    """
    if not DLQ_BUCKET_NAME:
        logger.warning(
            "DLQ bucket not configured, skipping file copy",
            extra=_log_extra(source_bucket=source_bucket, source_key=source_key),
        )
        return None

    s3 = get_s3_client()
    dlq_key = f"unrecognized/{datetime.now(timezone.utc).strftime('%Y/%m/%d')}/{uuid.uuid4()}-{source_key}"
    try:
        s3.copy_object(
            Bucket=DLQ_BUCKET_NAME,
            Key=dlq_key,
            CopySource={"Bucket": source_bucket, "Key": source_key},
            Metadata={"dlq-reason": reason, "source-bucket": source_bucket, "source-key": source_key},
            MetadataDirective="REPLACE",
        )
        dlq_uri = f"s3://{DLQ_BUCKET_NAME}/{dlq_key}"
        logger.info(
            "Moved unrecognized file to DLQ bucket",
            extra=_log_extra(dlq_uri=dlq_uri, reason=reason),
        )
        return dlq_uri
    except ClientError as exc:
        logger.error(
            "Failed to copy file to DLQ bucket",
            extra=_log_extra(
                dlq_bucket=DLQ_BUCKET_NAME,
                dlq_key=dlq_key,
                error_message=str(exc),
            ),
        )
        return None


# ---------------------------------------------------------------------------
# SQS Routing
# --------------------------------------------------------------------------


def route_to_queue(category: FormatCategory, message_body: dict) -> str:
    """Send file metadata to the appropriate SQS processing queue.

    Args:
        category: RASTER or VECTOR.
        message_body: The full message payload to send.

    Returns:
        The SQS message ID.

    Raises:
        QueueRoutingError: If the message cannot be sent.
    """
    queue_name = RASTER_QUEUE_NAME if category == FormatCategory.RASTER else VECTOR_QUEUE_NAME
    queue_url = get_queue_url(queue_name)
    sqs = get_sqs_client()

    try:
        response = sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message_body, default=str),
            MessageAttributes={
                "formatName": {
                    "StringValue": message_body.get("detectedFormat", "UNKNOWN"),
                    "DataType": "String",
                },
                "formatCategory": {
                    "StringValue": category.value,
                    "DataType": "String",
                },
                "detectionMethod": {
                    "StringValue": "magic-number",
                    "DataType": "String",
                },
                "sourceBucket": {
                    "StringValue": message_body.get("storageUri", "").split("/")[2],
                    "DataType": "String",
                },
            },
        )
        message_id = response["MessageId"]
        logger.info(
            "Routed file to processing queue",
            extra=_log_extra(
                queue_name=queue_name,
                message_id=message_id,
                format_name=message_body.get("detectedFormat"),
                file_key=message_body.get("fileName"),
            ),
        )
        return message_id
    except ClientError as exc:
        raise QueueRoutingError(
            f"Failed to send message to '{queue_name}': {exc}",
            queue_name=queue_name,
            file_key=message_body.get("fileName"),
        ) from exc


def send_to_dlq(error_details: dict, file_metadata: dict) -> Optional[str]:
    """Send error details to the Dead Letter Queue for unrecognized formats.

    Args:
        error_details: Serialized error information.
        file_metadata: Available metadata about the unrecognized file.

    Returns:
        The SQS message ID, or None if DLQ send fails.
    """
    dlq_url = get_queue_url(DLQ_QUEUE_NAME)
    sqs = get_sqs_client()

    dlq_message = {
        "event": "format_detection_failure",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "error": error_details,
        "fileMetadata": file_metadata,
    }

    try:
        response = sqs.send_message(
            QueueUrl=dlq_url,
            MessageBody=json.dumps(dlq_message, default=str),
            MessageAttributes={
                "errorType": {
                    "StringValue": "FatalFormatError",
                    "DataType": "String",
                },
                "sourceBucket": {
                    "StringValue": file_metadata.get("bucket", "unknown"),
                    "DataType": "String",
                },
            },
        )
        message_id = response["MessageId"]
        logger.warning(
            "Sent unrecognized file metadata to DLQ",
            extra=_log_extra(
                dlq_queue=DLQ_QUEUE_NAME,
                message_id=message_id,
                file_key=file_metadata.get("key"),
            ),
        )
        return message_id
    except ClientError as exc:
        logger.error(
            "Failed to send to DLQ — critical data loss risk",
            extra=_log_extra(
                dlq_queue=DLQ_QUEUE_NAME,
                error_message=str(exc),
                file_key=file_metadata.get("key"),
            ),
        )
        return None


# ---------------------------------------------------------------------------
# Message Assembly
# --------------------------------------------------------------------------


def build_routing_message(
    bucket: str,
    key: str,
    detected_format: Any,
    object_meta: dict,
    sha256_checksum: str,
) -> dict[str, Any]:
    """Build the SQS message payload conforming to uploaded-file.schema.json.

    Args:
        bucket: S3 bucket name.
        key: S3 object key.
        detected_format: DetectionResult from magic number analysis.
        object_meta: Metadata from S3 head_object.
        sha256_checksum: Computed SHA-256 hex digest.

    Returns:
        Dict matching the uploaded-file schema contract.
    """
    # Extract user-uploaded metadata from S3 object tags (if any)
    user_metadata = object_meta.get("user_metadata", {})

    # Determine the queue name for routing reference
    queue_name = (
        RASTER_QUEUE_NAME
        if detected_format.category == FormatCategory.RASTER
        else VECTOR_QUEUE_NAME
    )

    return {
        "fileId": str(uuid.uuid4()),
        "fileName": key.split("/")[-1] if "/" in key else key,
        "fileSizeBytes": object_meta.get("content_length", 0),
        "mimeType": detected_format.mime_type,
        "storageUri": f"s3://{bucket}/{key}",
        "sha256Checksum": sha256_checksum,
        "uploadedBy": {
            "userId": user_metadata.get("user-id", "system"),
            "email": user_metadata.get("user-email", "system@arch-intel.internal"),
        },
        "uploadedAt": (
            object_meta.get("last_modified").isoformat()
            if object_meta.get("last_modified")
            else datetime.now(timezone.utc).isoformat()
        ),
        "metadata": {
            "projectName": user_metadata.get("project-name", "unspecified"),
            "clientName": user_metadata.get("client-name", "unspecified"),
            "buildingType": user_metadata.get("building-type", "unspecified"),
        },
        "formatDetection": detected_format.to_dict(),
        "routedTo": queue_name,
        "ingestedAt": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Core Processing — Per-Record Handler
# --------------------------------------------------------------------------


def process_s3_record(record: dict[str, Any]) -> dict[str, Any]:
    """Process a single S3 event record through the full detection pipeline.

    Pipeline stages:
      1. Extract bucket/key from the event record.
      2. Read file header bytes (Range GET).
      3. Detect format via magic number matching.
      4. Compute SHA-256 checksum.
      5. Build routing message.
      6. Send to the appropriate SQS queue.

    On unrecognized format:
      - Raises FatalFormatError after moving file to DLQ and sending error metadata.

    Args:
        record: A single record from the S3 Event Notification.

    Returns:
        Dict with processing result details.

    Raises:
        FatalFormatError: If the file format is unrecognized.
        S3AccessError: If the file cannot be read from S3.
        QueueRoutingError: If the message cannot be sent to SQS.
    """
    # ── Stage 1: Parse S3 event ──────────────────────────────────────────
    bucket = record["s3"]["bucket"]["name"]
    # S3 event keys are URL-encoded
    key = record["s3"]["object"]["key"].replace("+", " ").replace("%2F", "/").replace("%20", " ")
    object_size = record["s3"]["object"].get("size", 0)
    event_time = record.get("eventTime", datetime.now(timezone.utc).isoformat())

    logger.info(
        "Processing S3 event record",
        extra=_log_extra(
            bucket=bucket,
            key=key,
            object_size=object_size,
            event_time=event_time,
        ),
    )

    # ── Stage 2: Read file header ────────────────────────────────────────
    if object_size < 4:
        raise FileTooSmallError(
            f"File too small for format detection ({object_size} bytes)",
            file_size=object_size,
            file_key=key,
        )

    header_bytes = read_file_header(bucket, key, DETECTION_WINDOW_BYTES)
    header_hex = format_header_hex(header_bytes)

    logger.info(
        "Read file header for detection",
        extra=_log_extra(key=key, header_hex=header_hex, header_length=len(header_bytes)),
    )

    # ── Stage 3: Detect format via magic numbers ─────────────────────────
    detection = detect_format(header_bytes)

    if not detection.is_detected:
        # Format unrecognized — trigger DLQ path
        logger.warning(
            "Unrecognized file format — routing to DLQ",
            extra=_log_extra(
                key=key,
                bucket=bucket,
                header_hex=header_hex,
                detection=detection.to_dict(),
            ),
        )

        # Move file to DLQ bucket (if configured)
        dlq_uri = move_to_dlq_bucket(
            source_bucket=bucket,
            source_key=key,
            reason=f"Unrecognized format. Header: {header_hex}",
        )

        # Send error metadata to DLQ SQS queue
        error_info = FatalFormatError(
            message=f"Unrecognized file format for key '{key}'",
            file_key=key,
            bucket=bucket,
            magic_bytes=header_hex,
        )
        send_to_dlq(
            error_details=error_info.to_dict(),
            file_metadata={
                "bucket": bucket,
                "key": key,
                "size": object_size,
                "dlqUri": dlq_uri,
                "headerHex": header_hex,
                "eventTime": event_time,
            },
        )

        # Raise FatalFormatError — this is the terminal exception
        raise FatalFormatError(
            message=f"Unrecognized file format for '{key}' (header: {header_hex})",
            file_key=key,
            bucket=bucket,
            magic_bytes=header_hex,
        )

    logger.info(
        "Format detected successfully",
        extra=_log_extra(
            key=key,
            format_name=detection.format_name,
            category=detection.category.value,
            mime_type=detection.mime_type,
        ),
    )

    # ── Stage 4: Compute SHA-256 checksum ────────────────────────────────
    object_meta = get_object_metadata(bucket, key)
    sha256_checksum = compute_sha256(bucket, key)

    # ── Stage 5: Build routing message ───────────────────────────────────
    message = build_routing_message(
        bucket=bucket,
        key=key,
        detected_format=detection,
        object_meta=object_meta,
        sha256_checksum=sha256_checksum,
    )

    logger.info(
        "Built routing message",
        extra=_log_extra(
            file_id=message["fileId"],
            format_name=detection.format_name,
            category=detection.category.value,
            queue=message["routedTo"],
        ),
    )

    # ── Stage 6: Route to SQS ────────────────────────────────────────────
    message_id = route_to_queue(detection.category, message)

    return {
        "status": "ROUTED",
        "fileId": message["fileId"],
        "fileName": message["fileName"],
        "detectedFormat": detection.format_name,
        "category": detection.category.value,
        "mimeType": detection.mime_type,
        "queueName": message["routedTo"],
        "sqsMessageId": message_id,
        "storageUri": message["storageUri"],
    }


# ---------------------------------------------------------------------------
# Lambda Entry Point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda entry point for S3 Event Notification processing.

    Handles batched S3 events, processing each record independently.
    Per-record errors are captured without failing the entire batch,
    except for FatalFormatError which is recorded and re-raised for
    Lambda-level retry/DLQ behavior.

    Args:
        event: The S3 Event Notification payload.
        context: Lambda runtime context (function name, request ID, etc.).

    Returns:
        Summary dict with per-record results and overall status.
    """
    # Correlation ID from Lambda context for log tracing
    request_id = getattr(context, "aws_request_id", "local")
    function_name = getattr(context, "function_name", "file-ingestion")

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
        logger.warning("Lambda invoked with no S3 records", extra=_log_extra(request_id=request_id))
        return {"status": "NO_RECORDS", "results": [], "errorCount": 0}

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for idx, record in enumerate(records):
        record_id = f"{request_id}#{idx}"
        try:
            result = process_s3_record(record)
            result["recordId"] = record_id
            results.append(result)

        except FatalFormatError as exc:
            # Fatal format error — already sent to DLQ, log and record
            error_detail = {
                "recordId": record_id,
                "status": "FATAL_FORMAT_ERROR",
                "error": exc.to_dict(),
            }
            errors.append(error_detail)
            results.append(error_detail)
            logger.error(
                "FatalFormatError — file routed to DLQ",
                extra=_log_extra(
                    record_id=record_id,
                    error=str(exc),
                    file_key=exc.file_key,
                ),
            )

        except (S3AccessError, FileTooSmallError) as exc:
            error_detail = {
                "recordId": record_id,
                "status": "S3_ERROR",
                "error": exc.to_dict() if hasattr(exc, "to_dict") else str(exc),
            }
            errors.append(error_detail)
            results.append(error_detail)
            logger.error(
                "S3 access/min-size error during processing",
                extra=_log_extra(
                    record_id=record_id,
                    error=str(exc),
                ),
            )

        except QueueRoutingError as exc:
            error_detail = {
                "recordId": record_id,
                "status": "QUEUE_ROUTING_ERROR",
                "error": exc.to_dict(),
            }
            errors.append(error_detail)
            results.append(error_detail)
            logger.error(
                "SQS routing failure",
                extra=_log_extra(
                    record_id=record_id,
                    error=str(exc),
                    queue_name=exc.queue_name,
                ),
            )

        except Exception as exc:
            # Catch-all for unexpected errors
            error_detail = {
                "recordId": record_id,
                "status": "UNEXPECTED_ERROR",
                "error": {"errorType": type(exc).__name__, "errorMessage": str(exc)},
            }
            errors.append(error_detail)
            results.append(error_detail)
            logger.error(
                "Unexpected error during record processing",
                extra=_log_extra(record_id=record_id, error=str(exc)),
                exc_info=True,
            )

    # ── Build final summary ──────────────────────────────────────────────
    routed_count = sum(1 for r in results if r.get("status") == "ROUTED")
    error_count = len(errors)

    summary = {
        "status": "COMPLETED_WITH_ERRORS" if error_count > 0 else "COMPLETED",
        "totalRecords": len(records),
        "routedCount": routed_count,
        "errorCount": error_count,
        "results": results,
        "requestId": request_id,
    }

    logger.info(
        "Lambda invocation completed",
        extra=_log_extra(
            request_id=request_id,
            total_records=len(records),
            routed=routed_count,
            errors=error_count,
            status=summary["status"],
        ),
    )

    return summary