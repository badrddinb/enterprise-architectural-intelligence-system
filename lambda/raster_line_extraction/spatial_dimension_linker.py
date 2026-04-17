"""
Spatial Dimension Linker — Maps AWS Textract OCR Text to Architectural Line Segments.

Problem:
  Architectural plans contain dimension annotations (e.g., "15'-0\"") placed near the
  lines they measure.  After the raster pipeline extracts clean consolidated lines and
  AWS Textract OCR returns text blocks with bounding boxes, we need to mathematically
  determine which text label belongs to which line segment.

Solution:
  This module implements a geometry-first matching pipeline:
    1. Parse Textract blocks → extract centroid, text, orientation, confidence.
    2. Build a KDTree spatial index over line segment midpoints.
    3. For each text block, find candidate lines within max_distance via KDTree.
    4. Score candidates using perpendicular distance, angle alignment, and
       midpoint proximity — with one-to-one matching constraint.
    5. Enrich matched lines with explicit_dimension field.

Processing Rules:
  - Centroid Calculation: Center point (cx, cy) of the Textract bounding box.
  - Orthogonal Projection: Shortest perpendicular distance from text centroid
    to the nearest line segment (with endpoint clamping).
  - Angle Verification: Architectural dimensions are written parallel to the
    line they measure (or at 90° for perpendicular dimensions). Both cases
    are accepted within a configurable tolerance.
  - Proximity Threshold: Text centroid must be within max_distance of a line
    segment AND near the midpoint of that line.

Usage:
  linker = SpatialDimensionLinker(max_distance=50.0)
  enriched = linker.link_from_textract_response(
      lines=consolidated_lines,
      textract_response=textract_api_response,
      image_width=2480,
      image_height=3508,
  )

Dependencies:
  numpy>=1.24.0
  scipy>=1.10.0  (falls back to brute-force if unavailable)
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

# ──────────────────────────────────────────────────────────────────────────────
# Optional scipy import with graceful fallback
# ──────────────────────────────────────────────────────────────────────────────

try:
    from scipy.spatial import KDTree
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ──────────────────────────────────────────────────────────────────────────────
# Configuration Constants
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_MAX_DISTANCE: float = 50.0        # max perpendicular distance (px)
DEFAULT_MAX_ANGLE_DIFF: float = 15.0      # max angle difference (degrees)
DEFAULT_MIDPOINT_TOLERANCE: float = 0.6   # fraction of line length from midpoint
DEFAULT_CONFIDENCE_THRESHOLD: float = 0.5 # minimum Textract confidence (0-100)
DEFAULT_ANGLE_TOLERANCE: float = 2.0      # tolerance for "parallel" (degrees)
ORTHOGONAL_ANGLE: float = 90.0            # perpendicular dimension lines

# Scoring weights
W_DISTANCE: float = 0.5
W_ANGLE: float = 0.3
W_MIDPOINT: float = 0.2


# ──────────────────────────────────────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class TextractBlock:
    """Parsed Textract annotation with spatial metadata.

    Attributes:
        block_id:   Textract BlockIdentifier.
        text:       OCR-extracted text string (e.g., "15'-0\"").
        confidence: OCR confidence score (0-100).
        cx:         Centroid X in absolute pixel coordinates.
        cy:         Centroid Y in absolute pixel coordinates.
        angle:      Orientation angle in degrees (normalized to [-90, 90)).
                    Computed from polygon vertices. None if unavailable.
        width:      Bounding box width in pixels.
        height:     Bounding box height in pixels.
    """
    block_id: str
    text: str
    confidence: float
    cx: float
    cy: float
    angle: Optional[float] = None
    width: float = 0.0
    height: float = 0.0


@dataclass
class _LineSegment:
    """Internal line segment with precomputed spatial metadata."""
    id: str
    start: list[float]
    end: list[float]
    midx: float = 0.0
    midy: float = 0.0
    angle: float = 0.0
    length: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    nx: float = 0.0
    ny: float = 0.0

    def __post_init__(self) -> None:
        self.midx = (self.start[0] + self.end[0]) / 2.0
        self.midy = (self.start[1] + self.end[1]) / 2.0
        dx = self.end[0] - self.start[0]
        dy = self.end[1] - self.start[1]
        self.length = math.hypot(dx, dy)
        if self.length > 0:
            self.dx = dx / self.length
            self.dy = dy / self.length
            self.nx = -self.dy
            self.ny = self.dx
        self.angle = _normalize_angle(math.degrees(math.atan2(dy, dx)))


@dataclass
class _MatchCandidate:
    """A scored text→line match candidate."""
    text_idx: int
    line_idx: int
    score: float
    perp_distance: float
    angle_diff: float
    midpoint_offset: float


# ──────────────────────────────────────────────────────────────────────────────
# Geometry Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_angle(angle_deg: float) -> float:
    """Normalize angle to [-90, 90) for symmetric comparison.

    A line from A→B and B→A should report the same normalized angle.
    """
    angle = angle_deg % 360
    if angle > 270:
        angle -= 360
    elif angle > 90:
        angle -= 180
    if angle == -90.0:
        angle = 90.0
    return angle


def _angular_distance(a1: float, a2: float) -> float:
    """Compute the smallest angular difference between two normalized angles.

    Returns a value in [0, 90].
    """
    diff = abs(a1 - a2)
    return min(diff, 180.0 - diff)


def _point_to_segment_distance(
    px: float, py: float,
    x1: float, y1: float,
    x2: float, y2: float,
) -> tuple[float, float]:
    """Shortest distance from point (px, py) to line segment (x1,y1)→(x2,y2).

    Handles endpoint clamping: if the perpendicular projection falls outside
    the segment, the distance to the nearest endpoint is returned instead.

    Returns:
        (distance, projection_ratio) where projection_ratio is the parametric
        position of the closest point along the segment [0, 1].
    """
    dx = x2 - x1
    dy = y2 - y1
    len_sq = dx * dx + dy * dy

    if len_sq < 1e-12:
        # Degenerate segment (zero length)
        return math.hypot(px - x1, py - y1), 0.0

    # Parametric projection t ∈ [0, 1]
    t = ((px - x1) * dx + (py - y1) * dy) / len_sq
    t_clamped = max(0.0, min(1.0, t))

    # Closest point on segment
    closest_x = x1 + t_clamped * dx
    closest_y = y1 + t_clamped * dy

    dist = math.hypot(px - closest_x, py - closest_y)
    return dist, t_clamped


def _centroid_from_bbox(
    bbox: dict[str, float],
    img_w: float,
    img_h: float,
) -> tuple[float, float]:
    """Convert a normalized Textract BoundingBox to absolute pixel centroid.

    Textract BoundingBox: {"Width", "Height", "Left", "Top"} in [0, 1].
    """
    left = bbox.get("Left", 0.0)
    top = bbox.get("Top", 0.0)
    width = bbox.get("Width", 0.0)
    height = bbox.get("Height", 0.0)

    cx = (left + width / 2.0) * img_w
    cy = (top + height / 2.0) * img_h
    return cx, cy


def _centroid_from_polygon(
    polygon: list[dict[str, float]],
    img_w: float,
    img_h: float,
) -> tuple[float, float]:
    """Compute centroid from a Textract Polygon (list of {X, Y} points).

    Textract polygon coordinates are normalized [0, 1].
    """
    if not polygon:
        return 0.0, 0.0

    sum_x = sum(p.get("X", 0.0) for p in polygon)
    sum_y = sum(p.get("Y", 0.0) for p in polygon)
    n = len(polygon)

    return (sum_x / n) * img_w, (sum_y / n) * img_h


def _angle_from_polygon(
    polygon: list[dict[str, float]],
    img_w: float,
    img_h: float,
) -> Optional[float]:
    """Estimate text orientation angle from Textract polygon vertices.

    Uses the direction from the first to the second polygon vertex (top-left
    to top-right in typical Textract output). Returns normalized angle in
    degrees or None if insufficient vertices.
    """
    if len(polygon) < 2:
        return None

    # Use first two vertices (typically top-left → top-right)
    x1 = polygon[0].get("X", 0.0) * img_w
    y1 = polygon[0].get("Y", 0.0) * img_h
    x2 = polygon[1].get("X", 0.0) * img_w
    y2 = polygon[1].get("Y", 0.0) * img_h

    dx = x2 - x1
    dy = y2 - y1

    if math.hypot(dx, dy) < 1e-6:
        return None

    return _normalize_angle(math.degrees(math.atan2(dy, dx)))


def _is_dimension_text(text: str) -> bool:
    """Heuristic check: does the text look like an architectural dimension?

    Matches patterns like:
      "15'-0\"", "15'-0", "15'0\"", "180", "15'-6 1/2\"", "3 1/2\"", etc.
    """
    text = text.strip()
    if not text:
        return False

    # Feet-inches: 15'-0", 15'-0, 15'0"
    if re.match(r"^\d+['\"′]\s*[-–]?\s*\d+\s*['\"″]?$", text):
        return True

    # Feet only: 15', 15′
    if re.match(r"^\d+\s*['\"′]$", text):
        return True

    # Inches only: 6", 6″
    if re.match(r"^\d+\s*[\"″]$", text):
        return True

    # Fractional with units: 3 1/2", 15'-6 1/2"
    if re.match(r"^\d+['\"′]\s*[-–]?\s*\d+\s+\d+/\d+\s*['\"″]?$", text):
        return True

    # Fractional inches/units: 3 1/2", 1/4"
    if re.match(r"^\d+\s+\d+/\d+\s*['\"″]?$", text):
        return True

    # Plain fraction: 1/2, 3/4
    if re.match(r"^\d+/\d+$", text):
        return True

    # Plain numeric (could be mm/cm/m): 180, 4500
    if re.match(r"^\d{1,5}(\.\d+)?$", text):
        return True

    # With units: 15m, 450cm, 1800mm
    if re.match(r"^\d+(\.\d+)?\s*(mm|cm|m|in|ft|ft\s*in)$", text, re.IGNORECASE):
        return True

    # Feet and inches with fraction: 15'-6 1/2"
    if re.match(r"^\d+['\"′]\s*[-–]?\s*\d+(\s+\d+/\d+)?\s*['\"″]?$", text):
        return True

    return False


# ──────────────────────────────────────────────────────────────────────────────
# Textract Response Parser
# ──────────────────────────────────────────────────────────────────────────────

def _parse_textract_blocks(
    blocks: list[dict[str, Any]],
    img_w: float,
    img_h: float,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    filter_dimensions: bool = True,
) -> list[TextractBlock]:
    """Parse raw Textract Blocks into TextractBlock objects.

    Filters for WORD and LINE block types with confidence above threshold.
    If filter_dimensions is True, only blocks that look like dimension text
    are kept.

    Args:
        blocks:               List of Textract Block dicts.
        img_w:                Image width in pixels (for coord normalization).
        img_h:                Image height in pixels (for coord normalization).
        confidence_threshold: Minimum OCR confidence (0-100) to include.
        filter_dimensions:    If True, keep only dimension-like text.

    Returns:
        List of parsed TextractBlock objects.
    """
    parsed: list[TextractBlock] = []

    for block in blocks:
        block_type = block.get("BlockType", "")
        if block_type not in ("WORD", "LINE"):
            continue

        text = block.get("Text", "").strip()
        if not text:
            continue

        confidence = block.get("Confidence", 0.0)
        if confidence < confidence_threshold:
            continue

        # Optionally filter to dimension-like text only
        if filter_dimensions and not _is_dimension_text(text):
            continue

        geometry = block.get("Geometry", {})
        polygon = geometry.get("Polygon", [])
        bbox = geometry.get("BoundingBox", {})

        # Prefer polygon centroid (more accurate for rotated text)
        if polygon:
            cx, cy = _centroid_from_polygon(polygon, img_w, img_h)
            angle = _angle_from_polygon(polygon, img_w, img_h)
        else:
            cx, cy = _centroid_from_bbox(bbox, img_w, img_h)
            angle = None

        # Bounding box dimensions for reference
        bb_width = bbox.get("Width", 0.0) * img_w
        bb_height = bbox.get("Height", 0.0) * img_h

        parsed.append(TextractBlock(
            block_id=block.get("Id", str(uuid.uuid4())),
            text=text,
            confidence=confidence,
            cx=cx,
            cy=cy,
            angle=angle,
            width=bb_width,
            height=bb_height,
        ))

    return parsed


# ──────────────────────────────────────────────────────────────────────────────
# Core Linker
# ──────────────────────────────────────────────────────────────────────────────

class SpatialDimensionLinker:
    """Maps AWS Textract OCR dimension text to corresponding line segments.

    Uses spatial indexing (KDTree) and geometric scoring to match text
    annotations from Textract to the nearest eligible line segment.

    Matching criteria:
      1. Perpendicular distance from text centroid to line ≤ max_distance.
      2. Angle of text orientation is parallel (0°) or orthogonal (90°) to
         the line within max_angle_diff tolerance.
      3. Text centroid projects near the line's midpoint (within
         midpoint_tolerance × line_length).

    Args:
        max_distance:         Max perpendicular distance in pixels.
        max_angle_diff:       Max angle difference in degrees for matching.
        midpoint_tolerance:   Fraction of line length for midpoint proximity.
        confidence_threshold: Minimum Textract confidence (0-100).
        filter_dimensions:    If True, only process dimension-like text.
    """

    def __init__(
        self,
        *,
        max_distance: float = DEFAULT_MAX_DISTANCE,
        max_angle_diff: float = DEFAULT_MAX_ANGLE_DIFF,
        midpoint_tolerance: float = DEFAULT_MIDPOINT_TOLERANCE,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        filter_dimensions: bool = True,
    ) -> None:
        self.max_distance = max_distance
        self.max_angle_diff = max_angle_diff
        self.midpoint_tolerance = midpoint_tolerance
        self.confidence_threshold = confidence_threshold
        self.filter_dimensions = filter_dimensions

        # Diagnostics from last link() call
        self.stats: dict[str, Any] = {}

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def link(
        self,
        lines: list[dict[str, Any]],
        textract_blocks: list[TextractBlock],
    ) -> list[dict[str, Any]]:
        """Match pre-parsed TextractBlocks to line segments.

        Args:
        lines:          Line dicts with 'id', 'start' [x,y], 'end' [x,y].
                        Also accepts 'x1','y1','x2','y2' format.
        textract_blocks: Pre-parsed TextractBlock objects.

        Returns:
        Enriched line dicts with 'explicit_dimension' field when matched.
        """
        if not lines:
            self.stats = {"input_lines": 0, "text_blocks": 0, "matches": 0}
            return []

        # Stage 1: Parse lines into internal segments
        segments = self._parse_lines(lines)

        # Stage 2: Run matching pipeline
        matches = self._find_matches(segments, textract_blocks)

        # Stage 3: Build enriched output
        # Create a mapping: line_idx → best match
        line_matches: dict[int, _MatchCandidate] = {}
        for m in matches:
            if m.line_idx not in line_matches or m.score > line_matches[m.line_idx].score:
                line_matches[m.line_idx] = m

        # Also track which text blocks are consumed (one-to-one constraint)
        consumed_text: set[int] = set()
        # Re-sort matches by score descending for greedy one-to-one assignment
        matches_sorted = sorted(matches, key=lambda m: m.score, reverse=True)
        line_matches = {}
        for m in matches_sorted:
            if m.text_idx in consumed_text or m.line_idx in line_matches:
                continue
            line_matches[m.line_idx] = m
            consumed_text.add(m.text_idx)

        # Build output
        result = []
        for idx, seg in enumerate(segments):
            enriched = {
                "id": seg.id,
                "start": [round(seg.start[0], 2), round(seg.start[1], 2)],
                "end": [round(seg.end[0], 2), round(seg.end[1], 2)],
            }
            # Carry over original fields
            orig = lines[idx] if idx < len(lines) else {}
            for key in ("angle", "length"):
                if key in orig:
                    enriched[key] = orig[key]
                elif key == "angle":
                    enriched["angle"] = round(seg.angle, 2)
                elif key == "length":
                    enriched["length"] = round(seg.length, 2)

            if idx in line_matches:
                best = line_matches[idx]
                text_block = textract_blocks[best.text_idx]
                enriched["explicit_dimension"] = text_block.text
                enriched["dimension_confidence"] = round(text_block.confidence / 100.0, 3)
                enriched["dimension_source_block_id"] = text_block.block_id
                enriched["_match_score"] = round(best.score, 4)
                enriched["_match_perp_distance"] = round(best.perp_distance, 2)
                enriched["_match_angle_diff"] = round(best.angle_diff, 2)
                enriched["_match_midpoint_offset"] = round(best.midpoint_offset, 2)

            result.append(enriched)

        self.stats = {
            "input_lines": len(segments),
            "text_blocks": len(textract_blocks),
            "matches": len(line_matches),
            "unmatched_lines": len(segments) - len(line_matches),
            "unmatched_texts": len(textract_blocks) - len(consumed_text),
            "scipy_used": _HAS_SCIPY,
        }

        return result

    def link_from_textract_response(
        self,
        lines: list[dict[str, Any]],
        textract_response: dict[str, Any],
        image_width: float,
        image_height: float,
    ) -> list[dict[str, Any]]:
        """Match Textract API response to line segments (convenience API).

        Handles full Textract DetectDocumentText or AnalyzeDocument response,
        extracting WORD/LINE blocks, normalizing coordinates, and running
        the matching pipeline.

        Args:
        lines:             Line dicts with 'id', 'start', 'end'.
        textract_response: Full Textract API response dict.
        image_width:       Source image width in pixels.
        image_height:      Source image height in pixels.

        Returns:
        Enriched line dicts with 'explicit_dimension' field when matched.
        """
        blocks = textract_response.get("Blocks", [])
        parsed_blocks = _parse_textract_blocks(
            blocks=blocks,
            img_w=image_width,
            img_h=image_height,
            confidence_threshold=self.confidence_threshold,
            filter_dimensions=self.filter_dimensions,
        )

        return self.link(lines, parsed_blocks)

    def get_stats(self) -> dict[str, Any]:
        """Return diagnostic statistics from the last link() call."""
        return self.stats

    # ──────────────────────────────────────────────────────────────────────
    # Line Parsing
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_lines(lines: list[dict[str, Any]]) -> list[_LineSegment]:
        """Convert line dicts into _LineSegment objects."""
        segments: list[_LineSegment] = []
        for raw in lines:
            if "start" in raw and "end" in raw:
                start = list(raw["start"])
                end = list(raw["end"])
            else:
                start = [float(raw.get("x1", 0)), float(raw.get("y1", 0))]
                end = [float(raw.get("x2", 0)), float(raw.get("y2", 0))]

            seg_id = raw.get("id", str(uuid.uuid4()))
            seg = _LineSegment(id=seg_id, start=start, end=end)
            if seg.length > 0:
                segments.append(seg)
        return segments

    # ──────────────────────────────────────────────────────────────────────
    # Matching Engine
    # ──────────────────────────────────────────────────────────────────────

    def _find_matches(
        self,
        segments: list[_LineSegment],
        text_blocks: list[TextractBlock],
    ) -> list[_MatchCandidate]:
        """Find all valid text→line match candidates.

        Uses KDTree for efficient spatial lookup when scipy is available,
        otherwise falls back to brute-force with numpy vectorization.
        """
        if not segments or not text_blocks:
            return []

        # Build line midpoint array for spatial indexing
        midpoints = np.array([[s.midx, s.midy] for s in segments])

        # Expand search radius: max_distance + half the longest line
        # (text can be near an endpoint, not just the midpoint)
        max_line_len = max(s.length for s in segments)
        search_radius = self.max_distance + max_line_len * self.midpoint_tolerance

        candidates: list[_MatchCandidate] = []

        if _HAS_SCIPY and len(segments) > 10:
            candidates = self._find_matches_kdtree(
                segments, text_blocks, midpoints, search_radius
            )
        else:
            candidates = self._find_matches_bruteforce(
                segments, text_blocks
            )

        return candidates

    def _find_matches_kdtree(
        self,
        segments: list[_LineSegment],
        text_blocks: list[TextractBlock],
        midpoints: np.ndarray,
        search_radius: float,
    ) -> list[_MatchCandidate]:
        """KDTree-accelerated matching for large line sets."""
        tree = KDTree(midpoints)
        candidates: list[_MatchCandidate] = []

        for t_idx, tb in enumerate(text_blocks):
            # Query KDTree for nearby line midpoints
            point = np.array([tb.cx, tb.cy])
            indices = tree.query_ball_point(point, search_radius)

            for l_idx in indices:
                seg = segments[l_idx]
                candidate = self._score_candidate(t_idx, l_idx, tb, seg)
                if candidate is not None:
                    candidates.append(candidate)

        return candidates

    def _find_matches_bruteforce(
        self,
        segments: list[_LineSegment],
        text_blocks: list[TextractBlock],
    ) -> list[_MatchCandidate]:
        """Brute-force O(n×m) matching (suitable for small datasets)."""
        candidates: list[_MatchCandidate] = []

        for t_idx, tb in enumerate(text_blocks):
            for l_idx, seg in enumerate(segments):
                candidate = self._score_candidate(t_idx, l_idx, tb, seg)
                if candidate is not None:
                    candidates.append(candidate)

        return candidates

    def _score_candidate(
        self,
        text_idx: int,
        line_idx: int,
        text_block: TextractBlock,
        segment: _LineSegment,
    ) -> Optional[_MatchCandidate]:
        """Score a single (text, line) pair. Returns None if ineligible.

        Scoring components:
          1. Perpendicular distance from text centroid to line segment.
          2. Angle alignment (parallel or orthogonal).
          3. Midpoint proximity (projection near segment center).
        """
        # ── Criterion 1: Perpendicular distance ───────────────────────────
        dist, t_proj = _point_to_segment_distance(
            text_block.cx, text_block.cy,
            segment.start[0], segment.start[1],
            segment.end[0], segment.end[1],
        )

        if dist > self.max_distance:
            return None

        # ── Criterion 2: Angle alignment ──────────────────────────────────
        angle_diff = self._compute_angle_alignment(text_block, segment)

        if angle_diff > self.max_angle_diff:
            return None

        # ── Criterion 3: Midpoint proximity ───────────────────────────────
        # t_proj is [0, 1] along the segment; midpoint is at 0.5
        midpoint_offset = abs(t_proj - 0.5)

        # Scale by midpoint_tolerance: offset should be within tolerance/2
        if midpoint_offset > self.midpoint_tolerance / 2.0:
            return None

        # ── Compute composite score ────────────────────────────────────────
        # Distance score: 1.0 at dist=0, 0.0 at dist=max_distance
        dist_score = max(0.0, 1.0 - dist / self.max_distance) if self.max_distance > 0 else 1.0

        # Angle score: 1.0 at diff=0, 0.0 at diff=max_angle_diff
        angle_score = max(0.0, 1.0 - angle_diff / self.max_angle_diff) if self.max_angle_diff > 0 else 1.0

        # Midpoint score: 1.0 at center, decreasing toward edges
        mid_score = max(0.0, 1.0 - midpoint_offset / (self.midpoint_tolerance / 2.0)) if self.midpoint_tolerance > 0 else 1.0

        composite = (
            W_DISTANCE * dist_score
            + W_ANGLE * angle_score
            + W_MIDPOINT * mid_score
        )

        return _MatchCandidate(
            text_idx=text_idx,
            line_idx=line_idx,
            score=composite,
            perp_distance=dist,
            angle_diff=angle_diff,
            midpoint_offset=midpoint_offset,
        )

    def _compute_angle_alignment(
        self,
        text_block: TextractBlock,
        segment: _LineSegment,
    ) -> float:
        """Compute the angle difference between text and line orientations.

        Architectural dimensions are written either:
          - Parallel to the line (angle_text ≈ angle_line)
          - Orthogonal to the line (angle_text ≈ angle_line ± 90°)

        Returns the minimum angular difference considering both cases.
        If text angle is unavailable, returns 0.0 (assumes match).
        """
        if text_block.angle is None:
            # No orientation info available — skip angle check
            return 0.0

        line_angle = segment.angle
        text_angle = text_block.angle

        # Direct parallel alignment
        parallel_diff = _angular_distance(text_angle, line_angle)

        # Orthogonal alignment (text rotated 90° relative to line)
        text_shifted = _normalize_angle(text_angle + ORTHOGONAL_ANGLE)
        orthogonal_diff = _angular_distance(text_shifted, line_angle)

        # Also check the other orthogonal direction
        text_shifted_neg = _normalize_angle(text_angle - ORTHOGONAL_ANGLE)
        orthogonal_diff_neg = _angular_distance(text_shifted_neg, line_angle)

        return min(parallel_diff, orthogonal_diff, orthogonal_diff_neg)

    # ──────────────────────────────────────────────────────────────────────
    # Output Formatting
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def format_clean(
        enriched_lines: list[dict[str, Any]],
        *,
        include_debug: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip internal debug fields from enriched output.

        Removes keys prefixed with '_match_' unless include_debug is True.

        Args:
            enriched_lines: Output from link() or link_from_textract_response().
            include_debug:  If True, keep diagnostic fields.

        Returns:
            Cleaned line dicts suitable for schema-compliant output.
        """
        if include_debug:
            return enriched_lines

        clean: list[dict[str, Any]] = []
        for line in enriched_lines:
            filtered = {
                k: v for k, v in line.items()
                if not k.startswith("_match_")
            }
            clean.append(filtered)
        return clean


# ──────────────────────────────────────────────────────────────────────────────
# Convenience Function (single-call API)
# ──────────────────────────────────────────────────────────────────────────────

def link_dimensions(
    lines: list[dict[str, Any]],
    textract_response: dict[str, Any],
    image_width: float,
    image_height: float,
    *,
    max_distance: float = DEFAULT_MAX_DISTANCE,
    max_angle_diff: float = DEFAULT_MAX_ANGLE_DIFF,
    midpoint_tolerance: float = DEFAULT_MIDPOINT_TOLERANCE,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> list[dict[str, Any]]:
    """One-shot dimension linking: lines + Textract → enriched lines.

    Args:
        lines:               Line dicts with 'id', 'start', 'end'.
        textract_response:   Full Textract API response dict.
        image_width:         Source image width in pixels.
        image_height:        Source image height in pixels.
        max_distance:        Max perpendicular distance (px).
        max_angle_diff:      Max angle difference (degrees).
        midpoint_tolerance:  Fraction of line length from midpoint.
        confidence_threshold: Minimum OCR confidence (0-100).

    Returns:
        List of enriched line dicts with 'explicit_dimension' when matched.
    """
    linker = SpatialDimensionLinker(
        max_distance=max_distance,
        max_angle_diff=max_angle_diff,
        midpoint_tolerance=midpoint_tolerance,
        confidence_threshold=confidence_threshold,
    )
    return linker.link_from_textract_response(
        lines=lines,
        textract_response=textract_response,
        image_width=image_width,
        image_height=image_height,
    )


# ──────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 5:
        print(
            "Usage: python spatial_dimension_linker.py "
            "<lines.json> <textract.json> <image_width> <image_height> [output.json]",
            file=sys.stderr,
        )
        sys.exit(1)

    lines_path = sys.argv[1]
    textract_path = sys.argv[2]
    img_w = float(sys.argv[3])
    img_h = float(sys.argv[4])
    output_path = sys.argv[5] if len(sys.argv) > 5 else None

    with open(lines_path, "r", encoding="utf-8") as f:
        lines_data = json.load(f)

    with open(textract_path, "r", encoding="utf-8") as f:
        textract_data = json.load(f)

    result = link_dimensions(
        lines=lines_data,
        textract_response=textract_data,
        image_width=img_w,
        image_height=img_h,
    )

    output_json = json.dumps(result, indent=2)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(output_json)
        print(f"Linked dimensions: {len(result)} lines processed → {output_path}")
    else:
        print(output_json)