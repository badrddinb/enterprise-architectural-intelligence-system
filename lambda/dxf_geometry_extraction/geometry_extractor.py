"""
Core Computational Geometry Engine — DXF Entity Extraction.

Parses a DXF file using ezdxf (read-only, no rendering) and extracts every
LINE, LWPOLYLINE, and MTEXT entity into a structured representation of
mathematical edges, points, and dimension annotations.

Processing rules:
  - LINE      → 1 edge from (x1, y1) to (x2, y2)
  - LWPOLYLINE → N-1 sequential edges from consecutive vertices.
                 Closed polylines add a closing edge (last → first).
                 Bulge arcs are chorded (straight vertex-to-vertex).
  - MTEXT     → Text content + insertion point for dimension annotation extraction.

Rendering properties (layers, colors, line thicknesses, linetypes) are
intentionally discarded — only mathematical start/end coordinates are preserved.

Point deduplication uses coordinate rounding (configurable decimal precision)
to merge coincident vertices into a single canonical point with a shared UUID.
"""

import logging
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import ezdxf
from ezdxf.document import Drawing
from ezdxf.entities import Line, LWPolyline, MText

logger = logging.getLogger("dxf_geometry_extraction")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Decimal places for coordinate rounding during point deduplication.
# A value of 6 gives sub-micrometer precision for meter-scale drawings.
COORDINATE_PRECISION = int(os.environ.get("COORDINATE_PRECISION", "6"))

# Minimum edge length to include (filters out zero-length edges from
# degenerate polylines or rounding artifacts). In DXF drawing units.
MIN_EDGE_LENGTH = float(os.environ.get("MIN_EDGE_LENGTH", "1e-9"))

# Regex pattern to extract numeric values from MTEXT dimension strings.
# Matches: "5000", "5.2 m", "12'6"', "3/4", "1200 mm", etc.
DIMENSION_PATTERN = re.compile(
    r"([+-]?\d+(?:\.\d+)?)\s*(mm|cm|m|ft|in|'|\"|″|'')?", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractedPoint:
    """A unique coordinate point extracted from the DXF.

    Attributes:
        point_id: UUID uniquely identifying this point.
        x: X coordinate in DXF drawing units.
        y: Y coordinate in DXF drawing units.
        source_entity: DXF entity type that produced this point.
    """

    point_id: str
    x: float
    y: float
    source_entity: str


@dataclass(frozen=True)
class ExtractedEdge:
    """A directed edge (line segment) extracted from the DXF.

    Attributes:
        id: UUID uniquely identifying this edge.
        type: Semantic type of the edge (always "wall" for DXF geometry).
        start: [x, y] coordinate pair of the start point.
        end: [x, y] coordinate pair of the end point.
        start_point_id: UUID reference to the start ExtractedPoint.
        end_point_id: UUID reference to the end ExtractedPoint.
        length: Euclidean length of the edge.
        source_entity: DXF entity type that produced this edge.
    """

    id: str
    type: str
    start: list[float]
    end: list[float]
    start_point_id: str
    end_point_id: str
    length: float
    source_entity: str


@dataclass
class ExtractedAnnotation:
    """A text annotation extracted from MTEXT entities.

    Attributes:
        annotation_id: UUID uniquely identifying this annotation.
        text: Raw text content (with formatting stripped).
        x: X coordinate of the insertion point.
        y: Y coordinate of the insertion point.
        numeric_value: Parsed numeric value if the text contains a dimension.
        unit: Parsed unit string if detected.
    """

    annotation_id: str
    text: str
    x: float
    y: float
    numeric_value: Optional[float] = None
    unit: Optional[str] = None


@dataclass
class ExtractionResult:
    """Complete result of DXF geometry extraction.

    Attributes:
        points: All unique coordinate points.
        edges: All extracted edges (line segments).
        edges_flat: Flat JSON array of edges as specified by the task contract.
        annotations: All text annotations from MTEXT.
        entity_counts: Diagnostic counts of entities encountered by type.
    """

    points: list[ExtractedPoint] = field(default_factory=list)
    edges: list[ExtractedEdge] = field(default_factory=list)
    edges_flat: list[dict[str, Any]] = field(default_factory=list)
    annotations: list[ExtractedAnnotation] = field(default_factory=list)
    entity_counts: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Point Deduplication
# ---------------------------------------------------------------------------


class PointRegistry:
    """Registry for deduplicating coordinate points.

    Points with identical rounded coordinates are merged into a single
    canonical point. This ensures that shared vertices (e.g., where two
    LINE entities meet at a corner) produce a single unique point ID.
    """

    def __init__(self, precision: int = COORDINATE_PRECISION) -> None:
        self._precision = precision
        self._registry: dict[tuple[float, float], ExtractedPoint] = {}

    def _round_coord(self, x: float, y: float) -> tuple[float, float]:
        """Round coordinates to the configured precision for deduplication."""
        return (round(x, self._precision), round(y, self._precision))

    def register(self, x: float, y: float, source_entity: str) -> ExtractedPoint:
        """Register a point, returning the existing one if already seen.

        Args:
            x: X coordinate.
            y: Y coordinate.
            source_entity: The DXF entity type that produced this point.

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
            source_entity=source_entity,
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
# Edge Builder
# ---------------------------------------------------------------------------


def _edge_length(x1: float, y1: float, x2: float, y2: float) -> float:
    """Compute Euclidean distance between two points."""
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def build_edge(
    start_point: ExtractedPoint,
    end_point: ExtractedPoint,
    source_entity: str,
    edge_type: str = "wall",
) -> Optional[ExtractedEdge]:
    """Build an ExtractedEdge from two points, filtering zero-length edges.

    Args:
        start_point: The starting ExtractedPoint.
        end_point: The ending ExtractedPoint.
        source_entity: DXF entity type that produced this edge.
        edge_type: Semantic type classification (default: "wall").

    Returns:
        ExtractedEdge if the edge length exceeds MIN_EDGE_LENGTH, else None.
    """
    length = _edge_length(start_point.x, start_point.y, end_point.x, end_point.y)
    if length < MIN_EDGE_LENGTH:
        return None

    return ExtractedEdge(
        id=str(uuid.uuid4()),
        type=edge_type,
        start=[start_point.x, start_point.y],
        end=[end_point.x, end_point.y],
        start_point_id=start_point.point_id,
        end_point_id=end_point.point_id,
        length=round(length, COORDINATE_PRECISION),
        source_entity=source_entity,
    )


# ---------------------------------------------------------------------------
# Entity Extractors
# ---------------------------------------------------------------------------


def extract_lines(
    doc: Drawing,
    point_registry: PointRegistry,
    result: ExtractionResult,
) -> None:
    """Extract all LINE entities from every layout in the DXF document.

    Each LINE produces exactly one edge from its start to end point.

    Args:
        doc: The parsed ezdxf Drawing document.
        point_registry: Shared point deduplication registry.
        result: Accumulator for extracted geometry.
    """
    count = 0
    msp = doc.modelspace()
    for entity in msp.query("LINE"):
        line: Line = entity  # type: ignore[assignment]

        x1, y1 = line.dxf.start.x, line.dxf.start.y
        x2, y2 = line.dxf.end.x, line.dxf.end.y

        sp = point_registry.register(x1, y1, "LINE")
        ep = point_registry.register(x2, y2, "LINE")

        edge = build_edge(sp, ep, source_entity="LINE")
        if edge is not None:
            result.edges.append(edge)
            count += 1

    result.entity_counts["LINE"] = count
    logger.debug(f"Extracted {count} edges from LINE entities")


def extract_lwpolylines(
    doc: Drawing,
    point_registry: PointRegistry,
    result: ExtractionResult,
) -> None:
    """Extract all LWPOLYLINE entities from every layout in the DXF document.

    Each LWPOLYLINE is decomposed into sequential edges between consecutive
    vertices. If the polyline is closed, a closing edge is added from the
    last vertex back to the first.

    Bulge arcs (circular arc segments) are chorded — the arc is approximated
    by a single straight line from vertex to vertex, preserving only the
    mathematical start and end coordinates.

    Args:
        doc: The parsed ezdxf Drawing document.
        point_registry: Shared point deduplication registry.
        result: Accumulator for extracted geometry.
    """
    count = 0
    polyline_count = 0

    msp = doc.modelspace()
    for entity in msp.query("LWPOLYLINE"):
        lwpoly: LWPolyline = entity  # type: ignore[assignment]
        polyline_count += 1

        # Get all vertices as (x, y) tuples
        # lwpoly.get_points(format='xy') returns list of (x, y) tuples
        vertices = lwpoly.get_points(format="xy")

        if len(vertices) < 2:
            continue

        # Register all vertices as points
        points = []
        for vx, vy in vertices:
            pt = point_registry.register(vx, vy, "LWPOLYLINE")
            points.append(pt)

        # Build edges between consecutive vertices
        for i in range(len(points) - 1):
            edge = build_edge(points[i], points[i + 1], source_entity="LWPOLYLINE")
            if edge is not None:
                result.edges.append(edge)
                count += 1

        # If the polyline is closed, add a closing edge
        if lwpoly.closed and len(points) >= 3:
            edge = build_edge(points[-1], points[0], source_entity="LWPOLYLINE")
            if edge is not None:
                result.edges.append(edge)
                count += 1

    result.entity_counts["LWPOLYLINE_POLYLINES"] = polyline_count
    result.entity_counts["LWPOLYLINE_EDGES"] = count
    logger.debug(
        f"Extracted {count} edges from {polyline_count} LWPOLYLINE entities"
    )


def extract_mtext(
    doc: Drawing,
    result: ExtractionResult,
) -> None:
    """Extract all MTEXT entities from every layout in the DXF document.

    MTEXT entities are parsed for their plain text content and insertion
    coordinates. If the text contains a recognizable numeric dimension
    (e.g., "5000", "12.5 m", "30'-6"\"), it is parsed into a numeric value
    and unit for downstream dimension annotation processing.

    Args:
        doc: The parsed ezdxf Drawing document.
        result: Accumulator for extracted geometry.
    """
    count = 0

    msp = doc.modelspace()
    for entity in msp.query("MTEXT"):
        mtext: MText = entity  # type: ignore[assignment]

        # Get plain text content (strips DXF formatting codes)
        text = mtext.plain_text().strip()
        if not text:
            continue

        # Get insertion point
        insert = mtext.dxf.insert
        x, y = insert.x, insert.y

        # Attempt to parse a numeric dimension value from the text
        numeric_value, unit = _parse_dimension_text(text)

        annotation = ExtractedAnnotation(
            annotation_id=str(uuid.uuid4()),
            text=text,
            x=round(x, COORDINATE_PRECISION),
            y=round(y, COORDINATE_PRECISION),
            numeric_value=numeric_value,
            unit=unit,
        )
        result.annotations.append(annotation)
        count += 1

    result.entity_counts["MTEXT"] = count
    logger.debug(f"Extracted {count} MTEXT annotations")


def _parse_dimension_text(text: str) -> tuple[Optional[float], Optional[str]]:
    """Parse a numeric value and unit from a dimension text string.

    Handles formats like: "5000", "5.2 m", "12ft", "3/4\"", "1200 mm"

    Args:
        text: Plain text from an MTEXT entity.

    Returns:
        Tuple of (numeric_value, unit_string), or (None, None) if no
        numeric value could be parsed.
    """
    # Remove common DXF formatting artifacts and whitespace
    cleaned = text.strip().replace("\\P", " ").replace("\\X", " ")

    # Try to match a dimension pattern
    match = DIMENSION_PATTERN.search(cleaned)
    if match:
        try:
            value = float(match.group(1))
            # Use captured unit from regex group(2), or fall back to remainder
            captured_unit = match.group(2)
            if captured_unit:
                unit = _classify_unit(captured_unit)
            else:
                remainder = cleaned[match.end():].strip().lower()
                unit = _classify_unit(remainder) if remainder else None
            return value, unit
        except ValueError:
            pass

    return None, None


def _classify_unit(unit_text: str) -> Optional[str]:
    """Classify a unit string into a canonical unit identifier.

    Args:
        unit_text: Lowercase unit text (e.g., "mm", "meters", "ft").

    Returns:
        Canonical unit string matching the schema enum, or None.
    """
    unit_text = unit_text.strip().lower()

    unit_map = {
        # Millimeters
        "mm": "mm",
        "millimeter": "mm",
        "millimeters": "mm",
        # Centimeters
        "cm": "cm",
        "centimeter": "cm",
        "centimeters": "cm",
        # Meters
        "m": "m",
        "meter": "m",
        "meters": "m",
        "metre": "m",
        "metres": "m",
        # Inches
        "in": "in",
        "inch": "in",
        "inches": "in",
        '"': "in",
        "″": "in",
        "''": "in",
        # Feet
        "ft": "ft",
        "foot": "ft",
        "feet": "ft",
        "'": "ft",
        "'": "ft",
    }

    return unit_map.get(unit_text)


# ---------------------------------------------------------------------------
# Main Extraction Pipeline
# ---------------------------------------------------------------------------


def extract_geometry(dxf_path: str | Path) -> ExtractionResult:
    """Parse a DXF file and extract all geometric entities.

    This is the main entry point for the geometry extraction pipeline.
    It opens the DXF file in read-only mode (no rendering), iterates
    through all supported entity types, and returns a structured result.

    Pipeline stages:
      1. Open and parse the DXF file with ezdxf (read-only).
      2. Extract LINE entities → edges.
      3. Extract LWPOLYLINE entities → decomposed edges.
      4. Extract MTEXT entities → text annotations.
      5. Build flat edge array for the task-specified output format.
      6. Return the complete extraction result.

    Args:
        dxf_path: Path to the DXF file on disk.

    Returns:
        ExtractionResult containing all points, edges, and annotations.

    Raises:
        DXFParseError: If ezdxf cannot read or parse the file.
        EmptyDXFError: If the file contains no extractable geometric entities.
    """
    from exceptions import DXFParseError, EmptyDXFError

    dxf_path = Path(dxf_path)

    # ── Stage 1: Parse DXF file ──────────────────────────────────────────
    logger.info(
        f"Opening DXF file for parsing",
        extra={"dxf_path": str(dxf_path), "file_size": dxf_path.stat().st_size if dxf_path.exists() else -1},
    )

    try:
        doc = ezdxf.readfile(str(dxf_path))
    except ezdxf.DXFStructureError as exc:
        raise DXFParseError(
            message=f"DXF structure error: {exc}",
            file_key=dxf_path.name,
            parse_details=str(exc),
        ) from exc
    except ezdxf.DXFVersionError as exc:
        raise DXFParseError(
            message=f"Unsupported DXF version: {exc}",
            file_key=dxf_path.name,
            parse_details=str(exc),
        ) from exc
    except IOError as exc:
        raise DXFParseError(
            message=f"Cannot read DXF file: {exc}",
            file_key=dxf_path.name,
            parse_details=str(exc),
        ) from exc
    except Exception as exc:
        raise DXFParseError(
            message=f"Unexpected error parsing DXF: {exc}",
            file_key=dxf_path.name,
            parse_details=str(exc),
        ) from exc

    logger.info(
        f"DXF file parsed successfully",
        extra={
            "dxf_version": doc.dxfversion,
            "entity_count": len(list(doc.entities)),
        },
    )

    # ── Stage 2-4: Extract entities ──────────────────────────────────────
    point_registry = PointRegistry(precision=COORDINATE_PRECISION)
    result = ExtractionResult()

    extract_lines(doc, point_registry, result)
    extract_lwpolylines(doc, point_registry, result)
    extract_mtext(doc, result)

    # Collect all unique points
    result.points = point_registry.all_points()

    # ── Stage 5: Build flat edge array ───────────────────────────────────
    result.edges_flat = [
        {
            "id": edge.id,
            "type": edge.type,
            "start": edge.start,
            "end": edge.end,
        }
        for edge in result.edges
    ]

    # ── Validation ───────────────────────────────────────────────────────
    total_entities = sum(result.entity_counts.values())
    if total_entities == 0:
        raise EmptyDXFError(
            message=f"No extractable geometric entities found in DXF file: {dxf_path.name}",
            file_key=dxf_path.name,
            entity_counts=result.entity_counts,
        )

    logger.info(
        "Geometry extraction completed",
        extra={
            "unique_points": point_registry.size,
            "total_edges": len(result.edges),
            "total_annotations": len(result.annotations),
            "entity_counts": result.entity_counts,
        },
    )

    return result


def extraction_result_to_schema(
    result: ExtractionResult,
    source_file_id: str,
    source_zones_id: Optional[str] = None,
    coordinate_units: str = "meters",
) -> dict[str, Any]:
    """Convert ExtractionResult to the raw-coordinates.schema.json format.

    Builds the full schema-compliant output including:
      - points[] with UUID references and point classifications
      - lines[] with start/end point ID references and measured lengths
      - dimensionAnnotations[] parsed from MTEXT entities
      - coordinateSystem metadata
      - extractedBy provenance

    Args:
        result: The extraction result from extract_geometry().
        source_file_id: UUID of the source UploadedFile.
        source_zones_id: Optional UUID of the source CroppedZones.
        coordinate_units: Drawing units (default: "meters").

    Returns:
        Dict conforming to the raw-coordinates.schema.json contract.
    """
    try:
        from dxf_geometry_extraction import __version__
    except ImportError:
        from __init__ import __version__

    # Build points array
    points = []
    for pt in result.points:
        points.append({
            "pointId": pt.point_id,
            "x": pt.x,
            "y": pt.y,
            "sourceZoneId": source_zones_id or "00000000-0000-0000-0000-000000000000",
            "pointType": _classify_point_type(pt.source_entity),
            "confidence": 1.0,  # DXF parse is deterministic
        })

    # Build lines array
    lines = []
    for edge in result.edges:
        lines.append({
            "lineId": edge.id,
            "startPointId": edge.start_point_id,
            "endPointId": edge.end_point_id,
            "lineType": "wall",
            "measuredLength": edge.length,
            "confidence": 1.0,
        })

    # Build dimension annotations array
    annotations = []
    dummy_zone_id = source_zones_id or "00000000-0000-0000-0000-000000000000"
    for ann in result.annotations:
        if ann.numeric_value is not None:
            # MTEXT with a parsed numeric value → dimension annotation
            # We create synthetic start/end points at the annotation location
            start_pt_id = str(uuid.uuid4())
            end_pt_id = str(uuid.uuid4())

            # Add these annotation reference points to the points array
            points.append({
                "pointId": start_pt_id,
                "x": ann.x,
                "y": ann.y,
                "sourceZoneId": dummy_zone_id,
                "pointType": "reference",
                "confidence": 1.0,
            })
            points.append({
                "pointId": end_pt_id,
                "x": ann.x,
                "y": ann.y,
                "sourceZoneId": dummy_zone_id,
                "pointType": "reference",
                "confidence": 1.0,
            })

            annotations.append({
                "annotationId": ann.annotation_id,
                "value": ann.numeric_value,
                "unit": ann.unit or "mm",
                "startPointId": start_pt_id,
                "endPointId": end_pt_id,
                "sourceZoneId": dummy_zone_id,
                "confidence": 0.9,  # OCR-like confidence for parsed text
            })

    return {
        "coordinatesId": str(uuid.uuid4()),
        "sourceZonesId": source_zones_id or "00000000-0000-0000-0000-000000000000",
        "sourceFileId": source_file_id,
        "createdAt": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "extractedBy": {
            "method": "dxf-parse",
            "version": __version__,
        },
        "coordinateSystem": {
            "type": "cartesian-2d",
            "units": coordinate_units,
            "originX": 0.0,
            "originY": 0.0,
        },
        "points": points,
        "lines": lines,
        "dimensionAnnotations": annotations,
        # ── Convenience field: flat edge array (task-specified format) ────
        "edges": result.edges_flat,
    }


def _classify_point_type(source_entity: str) -> str:
    """Map a DXF entity type to a schema pointType enum value.

    Args:
        source_entity: The DXF entity type string (e.g., "LINE", "LWPOLYLINE").

    Returns:
        A valid pointType enum string from the raw-coordinates schema.
    """
    mapping = {
        "LINE": "endpoint",
        "LWPOLYLINE": "corner",
        "MTEXT": "reference",
    }
    return mapping.get(source_entity, "other")