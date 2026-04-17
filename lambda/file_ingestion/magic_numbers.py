"""
Magic Number (File Signature) detection module.

Reads the first bytes of a file and matches against a registry of known
binary signatures for architectural file formats. Signatures are checked
longest-first to prevent false prefix matches.

Supported formats:
  Raster: PDF, PNG, TIFF (little-endian & big-endian)
  Vector: DWG, DXF (binary & ASCII), IFC

References:
  - https://en.wikipedia.org/wiki/List_of_file_signatures
  - https://www.autodesk.com/developer-network/platform-technologies/dwg
  - https://standards.buildingsmart.org/IFC/DEV/IFC4_3/RC3/HTML/
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class FormatCategory(str, Enum):
    """Classification of detected file formats into processing pipelines."""

    RASTER = "RASTER"
    VECTOR = "VECTOR"


@dataclass(frozen=True)
class FormatSignature:
    """A single magic number entry in the signature registry.

    Attributes:
        name: Human-readable format name (e.g., "PDF", "DWG").
        category: RASTER or VECTOR — determines routing target.
        mime_type: Standard MIME type for the format.
        signature: Raw bytes to match at the start of the file.
        offset: Byte offset where the signature begins (typically 0).
        secondary: Optional secondary signature to confirm at a different offset.
                   If provided, BOTH primary and secondary must match.
    """

    name: str
    category: FormatCategory
    mime_type: str
    signature: bytes
    offset: int = 0
    secondary_signature: Optional[bytes] = None
    secondary_offset: Optional[int] = None


# ---------------------------------------------------------------------------
# Signature Registry
# Ordered by specificity: longest/most-specific signatures first.
# This prevents shorter signatures from falsely matching longer ones.
# ---------------------------------------------------------------------------
SIGNATURE_REGISTRY: list[FormatSignature] = [
    # ── VECTOR formats (checked first due to longer signatures) ──────────
    FormatSignature(
        name="IFC",
        category=FormatCategory.VECTOR,
        mime_type="application/x-ifc",
        signature=b"ISO-10303-21",
        offset=0,
        secondary_signature=b"FILE_PROTOCOL",
        secondary_offset=128,
    ),
    FormatSignature(
        name="DXF_BINARY",
        category=FormatCategory.VECTOR,
        mime_type="application/dxf",
        signature=b"AutoCAD Binary DXF",
        offset=0,
    ),
    FormatSignature(
        name="DWG",
        category=FormatCategory.VECTOR,
        mime_type="image/vnd.dwg",
        # DWG files start with "AC10" followed by version-specific bytes.
        # Matching "AC10" covers versions R13 through 2024+.
        signature=b"AC10",
        offset=0,
    ),
    FormatSignature(
        name="DXF_ASCII",
        category=FormatCategory.VECTOR,
        mime_type="application/dxf",
        # ASCII DXF files start with a section marker: "0\r\n" or "0\n"
        # followed by "SECTION" on the next line.
        signature=b"0\n",
        offset=0,
    ),
    # ── RASTER formats ───────────────────────────────────────────────────
    FormatSignature(
        name="PDF",
        category=FormatCategory.RASTER,
        mime_type="application/pdf",
        signature=b"%PDF-",
        offset=0,
    ),
    FormatSignature(
        name="PNG",
        category=FormatCategory.RASTER,
        mime_type="image/png",
        # 8-byte PNG signature: 89 50 4E 47 0D 0A 1A 0A
        signature=b"\x89PNG\r\n\x1a\n",
        offset=0,
    ),
    FormatSignature(
        name="TIFF_LE",
        category=FormatCategory.RASTER,
        mime_type="image/tiff",
        # TIFF Little-Endian: "II" + 0x2A00
        signature=b"II\x2a\x00",
        offset=0,
    ),
    FormatSignature(
        name="TIFF_BE",
        category=FormatCategory.RASTER,
        mime_type="image/tiff",
        # TIFF Big-Endian: "MM" + 0x002A
        signature=b"MM\x00\x2a",
        offset=0,
    ),
]


# Minimum number of bytes to read from S3 for reliable detection.
# The longest primary signature is "AutoCAD Binary DXF" = 18 bytes.
# IFC secondary check at offset 128 needs ~150 bytes.
# We read 256 bytes for safety and future extensibility.
DETECTION_WINDOW_BYTES = 256


@dataclass(frozen=True)
class DetectionResult:
    """Result of a magic number detection attempt.

    Attributes:
        format_name: Detected format name (e.g., "PDF", "DWG"), or None if unrecognized.
        category: RASTER or VECTOR, or None if unrecognized.
        mime_type: Detected MIME type, or "application/octet-stream" if unrecognized.
        confidence: Detection confidence. "exact" for magic-number match,
                    "none" for no match.
    """

    format_name: Optional[str]
    category: Optional[FormatCategory]
    mime_type: str
    confidence: str

    @property
    def is_detected(self) -> bool:
        return self.format_name is not None

    def to_dict(self) -> dict:
        return {
            "formatName": self.format_name,
            "category": self.category.value if self.category else None,
            "mimeType": self.mime_type,
            "confidence": self.confidence,
            "detectionMethod": "magic-number",
        }


def detect_format(file_header: bytes) -> DetectionResult:
    """Detect file format from the binary header using magic number matching.

    The function checks each signature in the registry (ordered longest-first)
    against the provided file header bytes. Both primary and optional secondary
    signatures must match for a positive identification.

    Args:
        file_header: The first N bytes of the file (recommend >= DETECTION_WINDOW_BYTES).

    Returns:
        DetectionResult with format details if matched, or a "not detected" result.
    """
    if not file_header or len(file_header) < 4:
        return DetectionResult(
            format_name=None,
            category=None,
            mime_type="application/octet-stream",
            confidence="none",
        )

    for sig in SIGNATURE_REGISTRY:
        # Calculate how many bytes we can actually check
        start = sig.offset
        end = start + len(sig.signature)

        if end > len(file_header):
            # Not enough bytes to check this signature — skip
            continue

        primary_match = file_header[start:end] == sig.signature

        if not primary_match:
            continue

        # If there's a secondary signature, verify it too
        if sig.secondary_signature is not None and sig.secondary_offset is not None:
            sec_start = sig.secondary_offset
            sec_end = sec_start + len(sig.secondary_signature)

            if sec_end > len(file_header):
                # Not enough bytes for secondary check — cannot confirm
                continue

            secondary_match = file_header[sec_start:sec_end] == sig.secondary_signature
            if not secondary_match:
                continue

        # Both checks passed (or no secondary required)
        return DetectionResult(
            format_name=sig.name,
            category=sig.category,
            mime_type=sig.mime_type,
            confidence="exact",
        )

    # No signature matched
    return DetectionResult(
        format_name=None,
        category=None,
        mime_type="application/octet-stream",
        confidence="none",
    )


def format_header_hex(file_header: bytes, max_bytes: int = 32) -> str:
    """Format file header bytes as a hex string for logging/diagnostics.

    Args:
        file_header: Raw bytes from the file start.
        max_bytes: Maximum number of bytes to include in the output.

    Returns:
        Space-separated hex string, e.g., "25 50 44 46 2D 31 2E 37".
    """
    return " ".join(f"{b:02X}" for b in file_header[:max_bytes])