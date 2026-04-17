"""
Custom exceptions for the File Ingestion Lambda.

All exceptions follow a consistent pattern:
  - FatalFormatError: File magic number does not match any supported format.
  - S3AccessError: Unrecoverable error accessing the source file in S3.
  - QueueRoutingError: Failure to deliver metadata to the target SQS queue.
  - FileTooSmallError: File is smaller than the minimum bytes needed for detection.
"""

from typing import Optional


class FatalFormatError(Exception):
    """Raised when a file's magic number does not match any supported format.

    This is a terminal error — the file is considered corrupt, mislabeled,
    or of an unsupported type. The file should be moved to the Dead Letter Queue.

    Attributes:
        file_key: S3 object key of the unrecognized file.
        bucket: S3 bucket name.
        magic_bytes: The raw bytes read from the file header (hex string).
        message: Human-readable error description.
    """

    def __init__(
        self,
        message: str,
        file_key: Optional[str] = None,
        bucket: Optional[str] = None,
        magic_bytes: Optional[str] = None,
    ) -> None:
        self.file_key = file_key
        self.bucket = bucket
        self.magic_bytes = magic_bytes
        self.message = message
        super().__init__(self.message)

    def __str__(self) -> str:
        parts = [self.message]
        if self.bucket and self.file_key:
            parts.append(f"s3://{self.bucket}/{self.file_key}")
        if self.magic_bytes:
            parts.append(f"magic_bytes=0x{self.magic_bytes}")
        return " | ".join(parts)

    def to_dict(self) -> dict:
        """Serialize error details for structured logging and DLQ messages."""
        return {
            "errorType": "FatalFormatError",
            "errorMessage": self.message,
            "fileKey": self.file_key,
            "bucket": self.bucket,
            "magicBytesHex": self.magic_bytes,
        }


class S3AccessError(Exception):
    """Raised when the Lambda cannot read an object from S3.

    This typically indicates a permissions issue, a deleted object,
    or a transient S3 failure that exhausted retries.
    """

    def __init__(self, message: str, bucket: Optional[str] = None, key: Optional[str] = None) -> None:
        self.bucket = bucket
        self.key = key
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "errorType": "S3AccessError",
            "errorMessage": str(self),
            "bucket": self.bucket,
            "key": self.key,
        }


class QueueRoutingError(Exception):
    """Raised when metadata cannot be delivered to the target SQS queue.

    After all retry attempts are exhausted, this error triggers
    the DLQ fallback path.
    """

    def __init__(self, message: str, queue_name: Optional[str] = None, file_key: Optional[str] = None) -> None:
        self.queue_name = queue_name
        self.file_key = file_key
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "errorType": "QueueRoutingError",
            "errorMessage": str(self),
            "queueName": self.queue_name,
            "fileKey": self.file_key,
        }


class FileTooSmallError(Exception):
    """Raised when a file is too small to contain a valid magic number.

    Any file smaller than the minimum detection window (4 bytes)
    cannot be reliably classified.
    """

    def __init__(self, message: str, file_size: int, file_key: Optional[str] = None) -> None:
        self.file_size = file_size
        self.file_key = file_key
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "errorType": "FileTooSmallError",
            "errorMessage": str(self),
            "fileSize": self.file_size,
            "fileKey": self.file_key,
        }