"""
Custom exceptions for the DXF Geometry Extraction Lambda.

All exceptions follow a consistent pattern with structured serialization:
  - DXFParseError: ezdxf failed to read or parse the DXF file.
  - S3DownloadError: Unrecoverable error downloading the file from S3.
  - EmptyDXFError: DXF parsed successfully but contains zero geometric entities.
  - InvalidInputError: Missing or malformed SQS message payload.
"""

from typing import Optional


class DXFParseError(Exception):
    """Raised when ezdxf cannot parse the DXF file.

    This indicates a corrupt, malformed, or incompatible DXF file.

    Attributes:
        file_key: S3 object key of the DXF file.
        bucket: S3 bucket name.
        parse_details: Additional details from ezdxf about the failure.
    """

    def __init__(
        self,
        message: str,
        file_key: Optional[str] = None,
        bucket: Optional[str] = None,
        parse_details: Optional[str] = None,
    ) -> None:
        self.file_key = file_key
        self.bucket = bucket
        self.parse_details = parse_details
        self.message = message
        super().__init__(self.message)

    def __str__(self) -> str:
        parts = [self.message]
        if self.bucket and self.file_key:
            parts.append(f"s3://{self.bucket}/{self.file_key}")
        if self.parse_details:
            parts.append(f"details={self.parse_details}")
        return " | ".join(parts)

    def to_dict(self) -> dict:
        """Serialize error details for structured logging and DLQ messages."""
        return {
            "errorType": "DXFParseError",
            "errorMessage": self.message,
            "fileKey": self.file_key,
            "bucket": self.bucket,
            "parseDetails": self.parse_details,
        }


class S3DownloadError(Exception):
    """Raised when the Lambda cannot download an object from S3.

    This typically indicates a permissions issue, a deleted object,
    or a transient S3 failure that exhausted retries.
    """

    def __init__(self, message: str, bucket: Optional[str] = None, key: Optional[str] = None) -> None:
        self.bucket = bucket
        self.key = key
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "errorType": "S3DownloadError",
            "errorMessage": str(self),
            "bucket": self.bucket,
            "key": self.key,
        }


class EmptyDXFError(Exception):
    """Raised when a DXF file contains no extractable geometric entities.

    The file parsed successfully but yielded zero LINE, LWPOLYLINE,
    or MTEXT entities — it may be an empty drawing or contain only
    unsupported entity types.
    """

    def __init__(
        self,
        message: str,
        file_key: Optional[str] = None,
        entity_counts: Optional[dict] = None,
    ) -> None:
        self.file_key = file_key
        self.entity_counts = entity_counts or {}
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "errorType": "EmptyDXFError",
            "errorMessage": str(self),
            "fileKey": self.file_key,
            "entityCounts": self.entity_counts,
        }


class InvalidInputError(Exception):
    """Raised when the SQS message payload is missing required fields.

    The message must contain at least a valid S3 URI and fileId
    to locate and identify the DXF file.
    """

    def __init__(self, message: str, missing_fields: Optional[list] = None) -> None:
        self.missing_fields = missing_fields or []
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "errorType": "InvalidInputError",
            "errorMessage": str(self),
            "missingFields": self.missing_fields,
        }