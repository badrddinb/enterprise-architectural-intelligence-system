"""
File Ingestion Lambda — Secure S3 file ingestion with magic-number-based format routing.

Part of the Enterprise Architectural Intelligence System.
Routes uploaded architectural plan files to the appropriate processing queue
based on binary file signature detection (not file extensions).
"""

__version__ = "1.0.0"