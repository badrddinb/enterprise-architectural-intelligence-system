"""
Custom exceptions for the Raster Line Extraction Lambda.

All exceptions follow a consistent pattern with structured serialization:
  - ImageProcessingError: OpenCV failed to process the image.
  - EmptyImageError: Image processed successfully but no lines were detected.
  - S3DownloadError: Unrecoverable error downloading the file from S3.
  - InvalidInputError: Missing or malformed SQS message payload.
"""

from typing import Optional


class ImageProcessingError(Exception):
    """Raised when OpenCV cannot process the image file.

    This indicates a corrupt, malformed, or incompatible image.

    Attributes:
        file_key: S3 object key of the image file.
        bucket: S3 bucket name.
        processing_details: Additional details about the failure.
    """

    def __init__(
        self,
        message: str,
        file_key: Optional[str] = None,
        bucket: Optional[str] = None,
        processing_details: Optional[str] = None,
    ) -> None:
        self.file_key = file_key
        self.bucket = bucket
        self.processing_details = processing_details
        self.message = message
        super().__init__(self.message)

    def __str__(self) -> str:
        parts = [self.message]
        if self.bucket and self.file_key:
            parts.append(f"s3://{self.bucket}/{self.file_key}")
        if self.processing_details:
            parts.append(f"details={self.processing_details}")
        return " | ".join(parts)

    def to_dict(self) -> dict:
        """Serialize error details for structured logging and DLQ messages."""
        return {
            "errorType": "ImageProcessingError",
            "errorMessage": self.message,
            "fileKey": self.file_key,
            "bucket": self.bucket,
            "processingDetails": self.processing_details,
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


class EmptyImageError(Exception):
    """Raised when an image yields zero lines after the full CV pipeline.

    The image was loaded and processed successfully but the Hough Line
    Transform detected no significant line segments — the image may be
    blank, extremely noisy, or contain only non-linear content.
    """

    def __init__(
        self,
        message: str,
        file_key: Optional[str] = None,
        pipeline_stats: Optional[dict] = None,
    ) -> None:
        self.file_key = file_key
        self.pipeline_stats = pipeline_stats or {}
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "errorType": "EmptyImageError",
            "errorMessage": str(self),
            "fileKey": self.file_key,
            "pipelineStats": self.pipeline_stats,
        }


class InvalidInputError(Exception):
    """Raised when the SQS message payload is missing required fields.

    The message must contain at least a valid S3 URI and fileId
    to locate and identify the image file.
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