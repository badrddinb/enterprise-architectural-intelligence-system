"""
Core Computer Vision Pipeline — Raster-to-Vector Line Extraction.

Reads a high-resolution raster image (flattened PDF/PNG/TIFF) and applies a
deterministic OpenCV pipeline to extract straight line segments representing
architectural elements (walls, grid lines, etc.).

Pipeline stages:
  1. Load image (grayscale if needed).
  2. Gaussian Blur — suppress scan noise and paper texture.
  3. Canny Edge Detection — isolate high-contrast boundaries.
  4. Morphological Close — bridge small gaps in wall lines.
  5. Probabilistic Hough Line Transform — detect straight segments.
  6. Affine Correction — deterministic angle snapping for orthogonal walls.
  7. Point deduplication — merge coincident endpoints.
  8. Schema conversion — output raw-coordinates.schema.json.

Rendering properties (colors, layers, line thicknesses) are intentionally
discarded — only mathematical start/end coordinates are preserved.

Optimized for large images (10k+ pixels per side) via adaptive downscaling
of the Hough transform stage while preserving full-resolution coordinates.
"""

import logging
import math
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import cv2
import numpy as np

from exceptions import EmptyImageError, ImageProcessingError

logger = logging.getLogger("raster_line_extraction")

# ---------------------------------------------------------------------------
# Configuration (overridable via environment variables)
# ---------------------------------------------------------------------------

# Gaussian blur kernel size (must be odd). Larger kernels suppress more noise
# but may blur fine architectural details.
GAUSSIAN_KERNEL_SIZE: int = int(os.environ.get("GAUSSIAN_KERNEL_SIZE", "5"))

# Canny edge detection: if auto_threshold is True, thresholds are computed
# from the image histogram using Otsu's method. Otherwise, fixed values.
CANNY_AUTO_THRESHOLD: bool = os.environ.get("CANNY_AUTO_THRESHOLD", "true").lower() == "true"
CANNY_THRESHOLD1: int = int(os.environ.get("CANNY_THRESHOLD1", "50"))
CANNY_THRESHOLD2: int = int(os.environ.get("CANNY_THRESHOLD2", "150"))

# Morphological closing kernel size (bridges small gaps in detected edges).
MORPH_KERNEL_SIZE: int = int(os.environ.get("MORPH_KERNEL_SIZE", "3"))

# Hough Line Transform parameters.
HOUGH_RHO: int = int(os.environ.get("HOUGH_RHO", "1"))                # Distance resolution (pixels)
HOUGH_THETA: float = float(os.environ.get("HOUGH_THETA", "1.0"))     # Angle resolution (radians) — 1° ≈ π/180
HOUGH_THRESHOLD: int = int(os.environ.get("HOUGH_THRESHOLD", "80"))   # Minimum vote count
HOUGH_MIN_LINE_LENGTH: int = int(os.environ.get("HOUGH_MIN_LINE_LENGTH", "50"))
HOUGH_MAX_LINE_GAP: int = int(os.environ.get("HOUGH_MAX_LINE_GAP", "10"))

# Adaptive downscaling: if the longest image dimension exceeds this value,
# the Hough transform runs on a downscaled copy for performance, then
# coordinates are mapped back to original resolution.
HOUGH_MAX_DIMENSION: int = int(os.environ.get("HOUGH_MAX_DIMENSION", "8000"))

# Minimum line length (in original image pixels) to include in output.
# Filters out tiny segments from noise or text strokes.
MIN_LINE_LENGTH: float = float(os.environ.get("MIN_LINE_LENGTH", "20.0"))

# Affine correction angular thresholds (degrees).
# Lines within ±1.5° of horizontal are snapped to exactly 0°.
# Lines within ±1.5° of vertical (88.5°–91.5°) are snapped to exactly 90°.
ANGLE_THRESHOLD_HORIZONTAL: float = float(os.environ.get("ANGLE_THRESHOLD_HORIZONTAL", "1.5"))
ANGLE_THRESHOLD_VERTICAL_LOW: float = float(os.environ.get("ANGLE_THRESHOLD_VERTICAL_LOW", "88.5"))
ANGLE_THRESHOLD_VERTICAL_HIGH: float = float(os.environ.get("ANGLE_THRESHOLD_VERTICAL_HIGH", "91.5"))

# Decimal places for coordinate rounding during point deduplication.
COORDINATE_PRECISION: int = int(os.environ.get("COORDINATE_PRECISION", "2"))


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DetectedLine:
    """A line segment detected by the Hough Transform with affine correction.

    Attributes:
        id: UUID uniquely identifying this line.
        start: [x, y] coordinate pair of the start point.
        end: [x, y] coordinate pair of the end point.
        original_start: [x, y] before affine correction (for diagnostics).
        original_end: [x, y] before affine correction (for diagnostics).
        angle_degrees: The corrected angle in degrees.
        length: Euclidean length of the corrected line in pixels.
        corrected: Whether affine correction was applied.
        hough_votes: Vote count from the Hough accumulator (diagnostic).
    """

    id: str
    start: list[float]
    end: list[float]
    original_start: list[float]
    original_end: list[float]
    angle_degrees: float
    length: float
    corrected: bool
    hough_votes: int = 0


@dataclass(frozen=True)
class ExtractedPoint:
    """A unique coordinate point extracted from line endpoints.

    Attributes:
        point_id: UUID uniquely identifying this point.
        x: X coordinate in image pixels.
        y: Y coordinate in image pixels.
        point_type: Classification of the point.
    """

    point_id: str
    x: float
    y: float
    point_type: str = "endpoint"


@dataclass
class ExtractionResult:
    """Complete result of the raster-to-vector extraction pipeline.

    Attributes:
        lines: All detected (and affine-corrected) line segments.
        points: All unique deduplicated endpoint coordinates.
        pipeline_stats: Diagnostic statistics from each pipeline stage.
        image_dimensions: (width, height) of the source image in pixels.
    """

    lines: list[DetectedLine] = field(default_factory=list)
    points: list[ExtractedPoint] = field(default_factory=list)
    pipeline_stats: dict[str, Any] = field(default_factory=dict)
    image_dimensions: tuple[int, int] = (0, 0)


# ---------------------------------------------------------------------------
# Point Deduplication
# ---------------------------------------------------------------------------


class PointRegistry:
    """Registry for deduplicating coordinate points from line endpoints.

    Points with identical rounded coordinates are merged into a single
    canonical point. This ensures that shared vertices (e.g., where two
    wall lines meet at a corner) produce a single unique point ID.
    """

    def __init__(self, precision: int = COORDINATE_PRECISION) -> None:
        self._precision = precision
        self._registry: dict[tuple[float, float], ExtractedPoint] = {}

    def _round_coord(self, x: float, y: float) -> tuple[float, float]:
        """Round coordinates to the configured precision for deduplication."""
        return (round(x, self._precision), round(y, self._precision))

    def register(self, x: float, y: float, point_type: str = "endpoint") -> ExtractedPoint:
        """Register a point, returning the existing one if already seen.

        Args:
            x: X coordinate.
            y: Y coordinate.
            point_type: Classification of the point.

        Returns:
            The canonical ExtractedPoint for this coordinate.
        """
        key = self._round_coord(x, y)
        if key in self._registry:
            return self._registry[key]

        point = ExtractedPoint(
            point_id=str(uuid.uuid4()),
            x=round(x, self._precision),
            y=round(y, self._precision),
            point_type=point_type,
        )
        self._registry[key] = point
        return point

    @property
    def size(self) -> int:
        """Number of unique points registered."""
        return len(self._registry)

    def all_points(self) -> list[ExtractedPoint]:
        """Return all unique points in insertion order."""
        return list(self._registry.values())


# ---------------------------------------------------------------------------
# Deterministic Affine Correction
# ---------------------------------------------------------------------------


def compute_line_angle(x1: float, y1: float, x2: float, y2: float) -> float:
    """Compute the angle of a line segment in degrees.

    Uses atan2(dy, dx) to get the angle in [-180, 180], then normalizes
    to [-90, 90] for orthogonal symmetry (a line from A→B has the same
    angle as B→A when normalized).

    Args:
        x1, y1: Start point coordinates.
        x2, y2: End point coordinates.

    Returns:
        Angle in degrees, normalized to [-90, 90].
    """
    dx = x2 - x1
    dy = y2 - y1
    angle = math.degrees(math.atan2(dy, dx))

    # Normalize to [-90, 90] for symmetric comparison
    if angle > 90:
        angle -= 180
    elif angle < -90:
        angle += 180

    return angle


def enforce_orthogonal_constraint(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    angle_threshold_h: float = ANGLE_THRESHOLD_HORIZONTAL,
    angle_threshold_v_low: float = ANGLE_THRESHOLD_VERTICAL_LOW,
    angle_threshold_v_high: float = ANGLE_THRESHOLD_VERTICAL_HIGH,
) -> tuple[float, float, float, float, float, bool]:
    """Deterministic affine correction for scanned document skew.

    Evaluates the angle of every detected line and forces near-horizontal
    or near-vertical lines to exact orthogonal angles. This mathematically
    corrects for scanner/photographer rotation without distorting genuinely
    diagonal lines.

    Rules:
        - angle ∈ [-1.5°, 1.5°]   → snap to exactly 0.0° (horizontal)
        - |angle| ∈ [88.5°, 91.5°] → snap to exactly 90.0° (vertical)
        - All other angles         → no correction

    After snapping the angle, the endpoint coordinates are recomputed
    deterministically by rotating the line to the target angle while
    preserving the original length and start point.

    Args:
        x1, y1: Start point coordinates.
        x2, y2: End point coordinates.
        angle_threshold_h: Max deviation from 0° for horizontal snap.
        angle_threshold_v_low: Lower bound for vertical snap (default 88.5°).
        angle_threshold_v_high: Upper bound for vertical snap (default 91.5°).

    Returns:
        Tuple of (new_x1, new_y1, new_x2, new_y2, corrected_angle, was_corrected).
    """
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)

    if length < 1e-9:
        return (x1, y1, x2, y2, 0.0, False)

    angle = compute_line_angle(x1, y1, x2, y2)

    # Determine target angle
    target_angle: Optional[float] = None

    if -angle_threshold_h <= angle <= angle_threshold_h:
        # Near-horizontal → snap to 0°
        target_angle = 0.0
    elif (angle_threshold_v_low <= abs(angle) <= angle_threshold_v_high):
        # Near-vertical → snap to 90°
        target_angle = 90.0 if angle > 0 else -90.0

    if target_angle is None:
        # No correction needed
        return (x1, y1, x2, y2, angle, False)

    # Check if the angle actually changed (avoid marking as corrected if already exact)
    actually_corrected = not math.isclose(angle, target_angle, abs_tol=1e-9)

    # Recompute endpoint at the target angle, preserving start point and length
    target_rad = math.radians(target_angle)
    new_x2 = x1 + length * math.cos(target_rad)
    new_y2 = y1 + length * math.sin(target_rad)

    return (x1, y1, new_x2, new_y2, target_angle, actually_corrected)


# ---------------------------------------------------------------------------
# Image Loading
# ---------------------------------------------------------------------------


def _convert_pdf_to_image(pdf_path: str | Path, dpi: int = 300) -> np.ndarray:
    """Convert the first page of a PDF to a grayscale numpy array.

    Uses PyMuPDF (fitz) for high-quality rendering without any system
    dependencies (no poppler required).

    Args:
        pdf_path: Path to the PDF file.
        dpi: Rendering resolution (default 300 for architectural accuracy).

    Returns:
        Grayscale image as a numpy ndarray (H×W, uint8).

    Raises:
        ImageProcessingError: If the PDF cannot be read or rendered.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise ImageProcessingError(
            message="PyMuPDF (fitz) is required for PDF processing. Install: pip install PyMuPDF",
            processing_details=f"pdf_path={pdf_path}",
        ) from exc

    pdf_path = Path(pdf_path)

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        raise ImageProcessingError(
            message=f"Cannot open PDF file: {pdf_path.name} — {exc}",
            processing_details=f"pdf_path={pdf_path}",
        ) from exc

    if len(doc) == 0:
        doc.close()
        raise ImageProcessingError(
            message=f"PDF file has no pages: {pdf_path.name}",
            processing_details=f"pdf_path={pdf_path}",
        )

    # Render first page at specified DPI
    # zoom factor: 72 dpi is default, so zoom = dpi / 72
    zoom = dpi / 72.0
    page = doc[0]
    mat = fitz.Matrix(zoom, zoom)
    pixmap = page.get_pixmap(matrix=mat)

    # Convert pixmap to numpy array (RGB)
    img_data = np.frombuffer(pixmap.samples, dtype=np.uint8)
    if pixmap.alpha:
        img_data = img_data.reshape(pixmap.height, pixmap.width, 4)
        img_data = img_data[:, :, :3]  # Drop alpha channel
    else:
        img_data = img_data.reshape(pixmap.height, pixmap.width, 3)

    # Convert RGB to grayscale
    gray = cv2.cvtColor(img_data, cv2.COLOR_RGB2GRAY)

    doc.close()

    logger.info(
        "PDF converted to image",
        extra={
            "pdf_path": str(pdf_path),
            "dpi": dpi,
            "width": gray.shape[1],
            "height": gray.shape[0],
        },
    )

    return gray


def load_image(image_path: str | Path) -> np.ndarray:
    """Load an image file as a grayscale numpy array.

    Supports PNG, TIFF, JPEG, BMP, and PDF formats. PDFs are rendered
    at 300 DPI using PyMuPDF before processing. The image is immediately
    converted to single-channel grayscale to reduce memory and prepare
    for edge detection.

    Args:
        image_path: Path to the image file on disk.

    Returns:
        Grayscale image as a numpy ndarray (H×W, uint8).

    Raises:
        ImageProcessingError: If the file cannot be read or is empty.
    """
    image_path = Path(image_path)

    if not image_path.exists():
        raise ImageProcessingError(
            message=f"Image file not found: {image_path}",
            processing_details=f"path={image_path}",
        )

    # Handle PDF files via PyMuPDF conversion
    if image_path.suffix.lower() == ".pdf":
        return _convert_pdf_to_image(image_path)

    # cv2.imread with IMREAD_GRAYSCALE loads directly as single-channel
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)

    if image is None:
        raise ImageProcessingError(
            message=f"OpenCV cannot read image: {image_path.name}",
            processing_details=(
                "Unsupported format or corrupt file. "
                "Supported: PNG, TIFF, JPEG, BMP, PDF."
            ),
        )

    logger.info(
        "Image loaded successfully",
        extra={
            "image_path": str(image_path),
            "width": image.shape[1],
            "height": image.shape[0],
            "dtype": str(image.dtype),
        },
    )

    return image


# ---------------------------------------------------------------------------
# CV Pipeline Stages
# ---------------------------------------------------------------------------


def apply_gaussian_blur(image: np.ndarray, kernel_size: int = GAUSSIAN_KERNEL_SIZE) -> np.ndarray:
    """Apply Gaussian blur to suppress scan noise and paper texture.

    Uses a symmetric kernel (must be odd). For architectural plans,
    a small kernel (3–7) suppresses scan artifacts without blurring
    fine wall lines.

    Args:
        image: Grayscale input image.
        kernel_size: Gaussian kernel size (must be odd, positive).

    Returns:
        Blurred image (same dimensions).
    """
    if kernel_size % 2 == 0:
        kernel_size += 1  # Ensure odd
    if kernel_size < 1:
        kernel_size = 1

    blurred = cv2.GaussianBlur(image, (kernel_size, kernel_size), 0)

    logger.debug(
        f"Gaussian blur applied: kernel={kernel_size}x{kernel_size}",
    )
    return blurred


def apply_canny_edge_detection(
    image: np.ndarray,
    auto_threshold: bool = CANNY_AUTO_THRESHOLD,
    threshold1: int = CANNY_THRESHOLD1,
    threshold2: int = CANNY_THRESHOLD2,
) -> np.ndarray:
    """Apply Canny edge detection to isolate high-contrast boundaries.

    When auto_threshold is enabled, Otsu's method is used to compute
    optimal thresholds from the image histogram. The high threshold is
    set to the Otsu value, and the low threshold is half of it (standard
    1:2 ratio recommended by Canny).

    Args:
        image: Grayscale (preferably blurred) input image.
        auto_threshold: Use Otsu auto-thresholding (default: True).
        threshold1: Low threshold for hysteresis (if not auto).
        threshold2: High threshold for hysteresis (if not auto).

    Returns:
        Binary edge map (H×W, uint8, values 0 or 255).
    """
    if auto_threshold:
        # Otsu's method returns the optimal threshold value
        otsu_thresh, _ = cv2.threshold(
            image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        threshold1 = int(otsu_thresh * 0.5)
        threshold2 = int(otsu_thresh)
        logger.debug(
            f"Canny auto-threshold: low={threshold1}, high={threshold2} "
            f"(Otsu={otsu_thresh:.1f})"
        )

    edges = cv2.Canny(image, threshold1, threshold2, apertureSize=3)

    edge_pixel_count = int(np.count_nonzero(edges))
    total_pixels = edges.shape[0] * edges.shape[1]
    edge_density = edge_pixel_count / total_pixels if total_pixels > 0 else 0

    logger.debug(
        f"Canny edges: {edge_pixel_count} edge pixels "
        f"({edge_density:.4f} density)"
    )

    return edges


def apply_morphological_close(
    edges: np.ndarray,
    kernel_size: int = MORPH_KERNEL_SIZE,
) -> np.ndarray:
    """Apply morphological closing to bridge small gaps in detected edges.

    Architectural scans often have broken wall lines due to noise,
    compression artifacts, or text overlapping. Closing (dilation followed
    by erosion) reconnects nearby edge segments without significantly
    altering their positions.

    Args:
        edges: Binary edge map from Canny detection.
        kernel_size: Size of the square structuring element.

    Returns:
        Closed edge map (same dimensions).
    """
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (kernel_size, kernel_size)
    )
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

    closed_pixels = int(np.count_nonzero(closed))
    logger.debug(
        f"Morphological close: kernel={kernel_size}x{kernel_size}, "
        f"edge_pixels={closed_pixels}"
    )

    return closed


def detect_hough_lines(
    edges: np.ndarray,
    rho: int = HOUGH_RHO,
    theta: float = HOUGH_THETA,
    threshold: int = HOUGH_THRESHOLD,
    min_line_length: int = HOUGH_MIN_LINE_LENGTH,
    max_line_gap: int = HOUGH_MAX_LINE_GAP,
    max_dimension: int = HOUGH_MAX_DIMENSION,
) -> tuple[np.ndarray, float]:
    """Detect straight line segments using Probabilistic Hough Line Transform.

    For very large images, the edge map is temporarily downscaled before
    running the Hough transform (which is O(N) in edge pixel count),
    then coordinates are mapped back to the original resolution.

    Args:
        edges: Binary edge map.
        rho: Distance resolution of the accumulator (pixels).
        theta: Angle resolution of the accumulator (radians).
        threshold: Accumulator threshold (minimum vote count).
        min_line_length: Minimum line segment length (pixels).
        max_line_gap: Maximum allowed gap between segments (pixels).
        max_dimension: Max dimension before adaptive downscaling.

    Returns:
        Tuple of:
          - numpy array of detected lines, shape (N, 4), each row [x1, y1, x2, y2]
            in original image coordinates.
          - scale factor used (1.0 if no downscaling).
    """
    h, w = edges.shape[:2]
    max_dim = max(h, w)
    scale = 1.0

    working_edges = edges

    if max_dim > max_dimension:
        scale = max_dimension / max_dim
        new_w = int(w * scale)
        new_h = int(h * scale)
        working_edges = cv2.resize(
            edges, (new_w, new_h), interpolation=cv2.INTER_NEAREST
        )
        logger.info(
            f"Adaptive downscale for Hough: {w}x{h} → {new_w}x{new_h} "
            f"(scale={scale:.4f})"
        )

    # Scale line parameters for downscaled image
    scaled_min_length = max(1, int(min_line_length * scale))
    scaled_max_gap = max(1, int(max_line_gap * scale))

    # Probabilistic Hough Line Transform
    lines = cv2.HoughLinesP(
        working_edges,
        rho=rho,
        theta=theta * (math.pi / 180.0),  # Convert degrees to radians
        threshold=threshold,
        minLineLength=scaled_min_length,
        maxLineGap=scaled_max_gap,
    )

    if lines is None or len(lines) == 0:
        logger.warning("Hough Line Transform detected no lines")
        return np.empty((0, 4), dtype=np.float64), scale

    # Reshape from (N, 1, 4) to (N, 4)
    lines = lines.reshape(-1, 4).astype(np.float64)

    # Scale coordinates back to original resolution
    if scale != 1.0:
        inv_scale = 1.0 / scale
        lines[:, [0, 2]] *= inv_scale  # x coordinates
        lines[:, [1, 3]] *= inv_scale  # y coordinates

    logger.info(
        f"Hough detected {len(lines)} raw line segments "
        f"(scale={scale:.4f})"
    )

    return lines, scale


# ---------------------------------------------------------------------------
# Main Extraction Pipeline
# ---------------------------------------------------------------------------


def extract_lines(image_path: str | Path) -> ExtractionResult:
    """Execute the full raster-to-vector extraction pipeline.

    Pipeline stages:
      1. Load image as grayscale.
      2. Gaussian blur to suppress noise.
      3. Canny edge detection (auto-thresholded via Otsu).
      4. Morphological closing to bridge gaps.
      5. Probabilistic Hough Line Transform.
      6. Deterministic affine (orthogonal) correction.
      7. Filter by minimum line length.
      8. Point deduplication.

    Args:
        image_path: Path to the raster image file on disk.

    Returns:
        ExtractionResult with all detected lines, unique points, and stats.

    Raises:
        ImageProcessingError: If the image cannot be loaded or processed.
        EmptyImageError: If no lines are detected after the full pipeline.
    """
    image_path = Path(image_path)
    stats: dict[str, Any] = {}

    # ── Stage 1: Load image ──────────────────────────────────────────────
    gray = load_image(image_path)
    h, w = gray.shape[:2]
    stats["image_dimensions"] = {"width": w, "height": h}
    stats["image_pixels"] = w * h

    # ── Stage 2: Gaussian Blur ───────────────────────────────────────────
    blurred = apply_gaussian_blur(gray)

    # ── Stage 3: Canny Edge Detection ────────────────────────────────────
    edges = apply_canny_edge_detection(blurred)
    stats["canny_edge_pixels"] = int(np.count_nonzero(edges))

    # ── Stage 4: Morphological Close ─────────────────────────────────────
    closed_edges = apply_morphological_close(edges)
    stats["morph_edge_pixels"] = int(np.count_nonzero(closed_edges))

    # ── Stage 5: Hough Line Transform ────────────────────────────────────
    raw_lines, scale = detect_hough_lines(closed_edges)
    stats["hough_raw_lines"] = len(raw_lines)
    stats["hough_scale_factor"] = scale

    if len(raw_lines) == 0:
        raise EmptyImageError(
            message=f"No lines detected in image: {image_path.name}",
            file_key=image_path.name,
            pipeline_stats=stats,
        )

    # ── Stage 6: Affine Correction + Filter ──────────────────────────────
    detected_lines: list[DetectedLine] = []
    corrected_count = 0
    filtered_count = 0

    for raw_line in raw_lines:
        x1, y1, x2, y2 = float(raw_line[0]), float(raw_line[1]), float(raw_line[2]), float(raw_line[3])

        # Skip degenerate segments
        line_length = math.hypot(x2 - x1, y2 - y1)
        if line_length < MIN_LINE_LENGTH:
            filtered_count += 1
            continue

        # Apply deterministic affine correction
        cx1, cy1, cx2, cy2, angle, was_corrected = enforce_orthogonal_constraint(
            x1, y1, x2, y2
        )

        corrected_length = math.hypot(cx2 - cx1, cy2 - cy1)
        if was_corrected:
            corrected_count += 1

        line = DetectedLine(
            id=str(uuid.uuid4()),
            start=[round(cx1, COORDINATE_PRECISION), round(cy1, COORDINATE_PRECISION)],
            end=[round(cx2, COORDINATE_PRECISION), round(cy2, COORDINATE_PRECISION)],
            original_start=[round(x1, COORDINATE_PRECISION), round(y1, COORDINATE_PRECISION)],
            original_end=[round(x2, COORDINATE_PRECISION), round(y2, COORDINATE_PRECISION)],
            angle_degrees=round(angle, 2),
            length=round(corrected_length, COORDINATE_PRECISION),
            corrected=was_corrected,
        )
        detected_lines.append(line)

    stats["lines_after_filter"] = len(detected_lines)
    stats["lines_affine_corrected"] = corrected_count
    stats["lines_filtered_short"] = filtered_count

    # ── Stage 7: Point Deduplication ─────────────────────────────────────
    point_registry = PointRegistry(precision=COORDINATE_PRECISION)

    for line in detected_lines:
        point_registry.register(line.start[0], line.start[1], "endpoint")
        point_registry.register(line.end[0], line.end[1], "endpoint")

    stats["unique_points"] = point_registry.size

    logger.info(
        "Raster line extraction completed",
        extra={
            "image": str(image_path),
            "raw_lines": stats["hough_raw_lines"],
            "detected_lines": len(detected_lines),
            "affine_corrected": corrected_count,
            "unique_points": point_registry.size,
        },
    )

    result = ExtractionResult(
        lines=detected_lines,
        points=point_registry.all_points(),
        pipeline_stats=stats,
        image_dimensions=(w, h),
    )

    # Attach point registry for downstream schema conversion
    result._point_registry = point_registry  # type: ignore[attr-defined]

    return result


# ---------------------------------------------------------------------------
# Schema Conversion
# ---------------------------------------------------------------------------


def extraction_result_to_schema(
    result: ExtractionResult,
    source_file_id: str,
    source_zones_id: Optional[str] = None,
) -> dict[str, Any]:
    """Convert ExtractionResult to the raw-coordinates.schema.json format.

    Builds the full schema-compliant output including:
      - points[] with UUID references and pixel origin traceability
      - lines[] with start/end point ID references and confidence scores
      - coordinateSystem metadata (pixel-based, cartesian-2d)
      - extractedBy provenance with CV pipeline details

    Args:
        result: The extraction result from extract_lines().
        source_file_id: UUID of the source UploadedFile.
        source_zones_id: Optional UUID of the source CroppedZones.

    Returns:
        Dict conforming to the raw-coordinates.schema.json contract.
    """
    try:
        from raster_line_extraction import __version__
    except ImportError:
        from __init__ import __version__

    dummy_zone_id = source_zones_id or "00000000-0000-0000-0000-000000000000"

    # Access the point registry from the result
    point_registry: PointRegistry = getattr(result, "_point_registry", PointRegistry())

    # ── Build points array ───────────────────────────────────────────────
    points = []
    for pt in result.points:
        points.append({
            "pointId": pt.point_id,
            "x": pt.x,
            "y": pt.y,
            "sourceZoneId": dummy_zone_id,
            "pointType": pt.point_type,
            "confidence": 0.95,  # CV-extracted, high but not deterministic
            "pixelOrigin": {
                "px": pt.x,
                "py": pt.y,
            },
        })

    # ── Build lines array with point ID references ───────────────────────
    lines_out = []
    edges_flat = []

    for detected_line in result.lines:
        # Look up the deduplicated point IDs
        sp = point_registry.register(detected_line.start[0], detected_line.start[1])
        ep = point_registry.register(detected_line.end[0], detected_line.end[1])

        # Compute confidence based on Hough votes and correction status
        # Uncorrected lines have higher confidence; corrected lines slightly less
        confidence = 0.95 if not detected_line.corrected else 0.90

        lines_out.append({
            "lineId": detected_line.id,
            "startPointId": sp.point_id,
            "endPointId": ep.point_id,
            "lineType": "wall",
            "measuredLength": detected_line.length,
            "confidence": confidence,
        })

        # Flat edge format: {"id": "uuid", "type": "wall", "start": [x1, y1], "end": [x2, y2]}
        edges_flat.append({
            "id": detected_line.id,
            "type": "wall",
            "start": detected_line.start,
            "end": detected_line.end,
        })

    return {
        "coordinatesId": str(uuid.uuid4()),
        "sourceZonesId": dummy_zone_id,
        "sourceFileId": source_file_id,
        "createdAt": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "extractedBy": {
            "method": "computer-vision",
            "version": __version__,
        },
        "coordinateSystem": {
            "type": "cartesian-2d",
            "units": "pixels",
            "originX": 0.0,
            "originY": 0.0,
            "rotationDegrees": 0.0,
        },
        "points": points,
        "lines": lines_out,
        "dimensionAnnotations": [],
        # ── Convenience field: flat edge array ────────────────────────────
        "edges": edges_flat,
        # ── Diagnostic metadata ───────────────────────────────────────────
        "pipelineStats": result.pipeline_stats,
        "imageDimensions": {
            "width": result.image_dimensions[0],
            "height": result.image_dimensions[1],
        },
    }