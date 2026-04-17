"""
Line Consolidator — Post-Hough Fragment Merging for Architectural Raster Extraction.

Problem:
  The Probabilistic Hough Line Transform outputs fragmented segments along a single
  drawn line due to PDF bounding boxes, anti-aliasing artifacts, and edge map gaps.
  A single architectural wall may produce 10–30 small overlapping segments.

Solution:
  This module implements a five-stage post-processing pipeline:
    1. Border Filtering    — Remove PDF page-border artifacts.
    2. Angle Grouping      — Cluster collinear segments by angle similarity.
    3. Spatial Merging     — Merge collinear, overlapping/nearby segments into one.
    4. Length Filtering    — Discard residual short segments.
    5. Centerline Collapse — Collapse parallel stroke-edge pairs into single centerlines.

Tuning Parameters:
  BORDER_THRESHOLD      — Pixel distance from image edge to consider a border line.
  ANGLE_TOLERANCE_DEG   — Max angular difference to consider lines parallel.
  PERPENDICULAR_DIST_PX — Max perpendicular distance for collinear grouping.
  ENDPOINT_GAP_PX       — Max gap between endpoints to merge overlapping spans.

Usage:
  consolidator = LineConsolidator(image_width=2480, image_height=3508)
  cleaned = consolidator.consolidate(raw_lines_json)
  print(json.dumps(cleaned, indent=2))
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np


# ──────────────────────────────────────────────────────────────────────────────
# Configuration Constants
# ──────────────────────────────────────────────────────────────────────────────

BORDER_THRESHOLD: float = 5.0        # px from absolute edge
ANGLE_TOLERANCE_DEG: float = 1.0     # degrees
PERPENDICULAR_DIST_PX: float = 5.0   # perpendicular distance
ENDPOINT_GAP_PX: float = 10.0        # gap between projected endpoint spans
MIN_MERGED_LENGTH: float = 10.0      # minimum length of output line
STROKE_THICKNESS_PX: float = 15.0    # max perpendicular distance for centerline collapse
COLLAPSE_ANGLE_TOL: float = 0.5      # degrees – tighter tolerance for parallel pairing
COLLAPSE_OVERLAP_RATIO: float = 0.5  # minimum projection-overlap ratio to pair lines


# ──────────────────────────────────────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class LineSegment:
    """A raw line segment with spatial and angular metadata.

    Attributes:
        id:           Unique identifier for traceability.
        start:        [x, y] start point.
        end:          [x, y] end point.
        angle:        Angle in degrees normalized to [-90, 90).
        length:       Euclidean length in pixels.
        projection:   Scalar projection of start and end onto the line's
                      own direction vector (used for overlap computation).
    """
    id: str
    start: list[float]
    end: list[float]
    angle: float = 0.0
    length: float = 0.0
    projection: tuple[float, float] = (0.0, 0.0)

    def __post_init__(self) -> None:
        dx = self.end[0] - self.start[0]
        dy = self.end[1] - self.start[1]
        self.length = math.hypot(dx, dy)
        self.angle = _normalize_angle(math.degrees(math.atan2(dy, dx)))


@dataclass
class MergeCluster:
    """A group of collinear segments awaiting merge.

    All segments in a cluster are within ANGLE_TOLERANCE_DEG of each other
    and within PERPENDICULAR_DIST_PX of a shared reference line.
    """
    segments: list[LineSegment] = field(default_factory=list)
    reference_angle: float = 0.0
    direction: list[float] = field(default_factory=lambda: [1.0, 0.0])
    normal: list[float] = field(default_factory=lambda: [0.0, 1.0])
    reference_point: list[float] = field(default_factory=lambda: [0.0, 0.0])


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
        angle = 90.0  # treat -90 as +90 (vertical symmetry)
    return angle


def _angular_distance(a1: float, a2: float) -> float:
    """Compute the smallest angular difference between two normalized angles.

    Returns a value in [0, 90].
    """
    diff = abs(a1 - a2)
    return min(diff, 180.0 - diff)


def _perpendicular_distance(
    px: float, py: float,
    rx: float, ry: float,
    nx: float, ny: float,
) -> float:
    """Signed perpendicular distance from point (px, py) to a line defined
    by reference point (rx, ry) and unit normal (nx, ny).

    Returns absolute distance.
    """
    return abs((px - rx) * nx + (py - ry) * ny)


def _project_onto_axis(
    px: float, py: float,
    rx: float, ry: float,
    dx: float, dy: float,
) -> float:
    """Scalar projection of vector (px-rx, py-ry) onto direction (dx, dy)."""
    return (px - rx) * dx + (py - ry) * dy


def _line_from_angle_point(
    angle_deg: float,
    px: float, py: float,
) -> tuple[list[float], list[float]]:
    """Compute unit direction and unit normal for a line at the given angle
    passing through point (px, py).
    """
    rad = math.radians(angle_deg)
    direction = [math.cos(rad), math.sin(rad)]
    normal = [-math.sin(rad), math.cos(rad)]
    return direction, normal


# ──────────────────────────────────────────────────────────────────────────────
# Core Consolidator
# ──────────────────────────────────────────────────────────────────────────────

class LineConsolidator:
    """Post-Hough line consolidation pipeline.

    Cleans, filters, and merges fragmented line segments from the OpenCV
    raster extraction pipeline into architecturally meaningful lines.

    Args:
        image_width:   Source image width in pixels.
        image_height:  Source image height in pixels.
        border_threshold:      Pixel distance from edge for border filtering.
        angle_tolerance_deg:   Max angle difference for parallel classification.
        perp_dist_px:          Max perpendicular distance for collinear grouping.
        endpoint_gap_px:       Max gap between projected spans for merging.
        min_merged_length:     Minimum length of output consolidated lines.
    """

    def __init__(
        self,
        image_width: int,
        image_height: int,
        *,
        border_threshold: float = BORDER_THRESHOLD,
        angle_tolerance_deg: float = ANGLE_TOLERANCE_DEG,
        perp_dist_px: float = PERPENDICULAR_DIST_PX,
        endpoint_gap_px: float = ENDPOINT_GAP_PX,
        min_merged_length: float = MIN_MERGED_LENGTH,
        stroke_thickness: float = STROKE_THICKNESS_PX,
        collapse_angle_tol: float = COLLAPSE_ANGLE_TOL,
        collapse_overlap_ratio: float = COLLAPSE_OVERLAP_RATIO,
    ) -> None:
        self.image_width = image_width
        self.image_height = image_height
        self.border_threshold = border_threshold
        self.angle_tolerance = angle_tolerance_deg
        self.perp_dist = perp_dist_px
        self.endpoint_gap = endpoint_gap_px
        self.min_merged_length = min_merged_length
        self.stroke_thickness = stroke_thickness
        self.collapse_angle_tol = collapse_angle_tol
        self.collapse_overlap_ratio = collapse_overlap_ratio

        # Statistics for diagnostics
        self.stats: dict[str, Any] = {}

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def consolidate(
        self,
        raw_lines: list[dict[str, Any]] | str,
    ) -> list[dict[str, Any]]:
        """Execute the full consolidation pipeline.

        Args:
            raw_lines: JSON array of line dicts, each with 'start' and 'end'
                       keys as [x, y] coordinate pairs.  Accepts either a
                       Python list or a JSON string.

        Returns:
            Consolidated JSON-compatible list of line dicts with 'id',
            'start', and 'end' keys.
        """
        # Parse input
        if isinstance(raw_lines, str):
            raw_lines = json.loads(raw_lines)

        input_count = len(raw_lines)
        self.stats["input_lines"] = input_count

        if input_count == 0:
            self.stats["border_filtered"] = 0
            self.stats["clusters_formed"] = 0
            self.stats["output_lines"] = 0
            return []

        # Stage 1: Parse into LineSegment objects
        segments = self._parse_segments(raw_lines)

        # Stage 2: Border filtering
        segments, border_removed = self._filter_borders(segments)
        self.stats["border_filtered"] = border_removed

        # Stage 3: Collinear clustering + merge
        merged = self._cluster_and_merge(segments)

        # Stage 4: Filter tiny residuals
        filtered = [s for s in merged if s.length >= self.min_merged_length]

        # Stage 5: Centerline collapse — merge parallel stroke-edge pairs
        final, pairs_collapsed = self._collapse_parallel_pairs(filtered)
        self.stats["pairs_collapsed"] = pairs_collapsed
        self.stats["output_lines"] = len(final)
        self.stats["consolidation_ratio"] = (
            round(input_count / len(final), 2) if final else 0
        )

        return self._format_output(final)

    def get_stats(self) -> dict[str, Any]:
        """Return diagnostic statistics from the last consolidation run."""
        return self.stats

    # ──────────────────────────────────────────────────────────────────────
    # Stage 1: Parsing
    # ──────────────────────────────────────────────────────────────────────

    def _parse_segments(
        self, raw_lines: list[dict[str, Any]]
    ) -> list[LineSegment]:
        """Convert raw line dicts into LineSegment objects with computed
        angle, length, and projection metadata.
        """
        segments: list[LineSegment] = []
        for raw in raw_lines:
            # Support both {"start": [x,y], "end": [x,y]} and
            # {"x1": ..., "y1": ..., "x2": ..., "y2": ...}
            if "start" in raw and "end" in raw:
                start = list(raw["start"])
                end = list(raw["end"])
            else:
                start = [float(raw.get("x1", 0)), float(raw.get("y1", 0))]
                end = [float(raw.get("x2", 0)), float(raw.get("y2", 0))]

            seg_id = raw.get("id", str(uuid.uuid4()))
            seg = LineSegment(id=seg_id, start=start, end=end)
            if seg.length > 0:
                segments.append(seg)
        return segments

    # ──────────────────────────────────────────────────────────────────────
    # Stage 2: Border Filtering
    # ──────────────────────────────────────────────────────────────────────

    def _filter_borders(
        self, segments: list[LineSegment]
    ) -> tuple[list[LineSegment], int]:
        """Remove lines that fall within the border threshold of the image
        edges (PDF page-frame artifacts).

        A line is removed if *both* its endpoints are within the border
        threshold of the same edge.  This preserves genuine lines that cross
        the border region while eliminating border framing artifacts.
        """
        w = float(self.image_width)
        h = float(self.image_height)
        t = self.border_threshold

        kept: list[LineSegment] = []
        removed = 0

        for seg in segments:
            sx, sy = seg.start
            ex, ey = seg.end

            # Check if both endpoints are near the same border
            near_left   = (sx <= t and ex <= t)
            near_right  = (sx >= w - t and ex >= w - t)
            near_top    = (sy <= t and ey <= t)
            near_bottom = (sy >= h - t and ey >= h - t)

            if near_left or near_right or near_top or near_bottom:
                removed += 1
            else:
                kept.append(seg)

        return kept, removed

    # ──────────────────────────────────────────────────────────────────────
    # Stage 3: Collinear Clustering + Merge
    # ──────────────────────────────────────────────────────────────────────

    def _cluster_and_merge(
        self, segments: list[LineSegment]
    ) -> list[LineSegment]:
        """Group segments into collinear clusters and merge each cluster.

        Algorithm:
          1. Sort segments by normalized angle for locality.
          2. Greedily assign each segment to the first matching cluster
             (angle within tolerance, perpendicular distance within tolerance).
          3. For each cluster, project all endpoints onto the shared axis
             and compute the spanning merge line.
        """
        if not segments:
            return []

        # Sort by angle for more efficient greedy clustering
        segments.sort(key=lambda s: s.angle)

        clusters: list[MergeCluster] = []

        for seg in segments:
            assigned = False
            for cluster in clusters:
                if self._assign_to_cluster(seg, cluster):
                    assigned = True
                    break
            if not assigned:
                self._create_cluster(seg, clusters)

        self.stats["clusters_formed"] = len(clusters)

        # Merge each cluster into a single consolidated line
        merged: list[LineSegment] = []
        for cluster in clusters:
            result = self._merge_cluster(cluster)
            if result is not None:
                merged.append(result)

        return merged

    def _assign_to_cluster(
        self, seg: LineSegment, cluster: MergeCluster
    ) -> bool:
        """Try to assign a segment to an existing cluster.

        Checks:
          1. Angular distance within tolerance.
          2. Both endpoints within perpendicular distance of the cluster's
             reference line.
        """
        # Check angle
        if _angular_distance(seg.angle, cluster.reference_angle) > self.angle_tolerance:
            return False

        # Check perpendicular distance of both endpoints
        rx, ry = cluster.reference_point
        nx, ny = cluster.normal

        d_start = _perpendicular_distance(
            seg.start[0], seg.start[1], rx, ry, nx, ny
        )
        d_end = _perpendicular_distance(
            seg.end[0], seg.end[1], rx, ry, nx, ny
        )

        if d_start <= self.perp_dist and d_end <= self.perp_dist:
            cluster.segments.append(seg)
            return True

        return False

    def _create_cluster(
        self, seg: LineSegment, clusters: list[MergeCluster]
    ) -> None:
        """Create a new cluster seeded by the given segment."""
        direction, normal = _line_from_angle_point(
            seg.angle, seg.start[0], seg.start[1]
        )
        cluster = MergeCluster(
            segments=[seg],
            reference_angle=seg.angle,
            direction=direction,
            normal=normal,
            reference_point=list(seg.start),
        )
        clusters.append(cluster)

    def _merge_cluster(
        self, cluster: MergeCluster
    ) -> LineSegment | None:
        """Merge all segments in a cluster into a single consolidated line.

        For each segment, both endpoints are projected onto the cluster's
        direction axis.  The merged line spans from the minimum to maximum
        projection, but only if the projected spans overlap or are within
        the endpoint gap tolerance.

        If the cluster has only one segment, it is returned as-is.
        """
        segs = cluster.segments
        if not segs:
            return None

        if len(segs) == 1:
            return segs[0]

        dx, dy = cluster.direction
        rx, ry = cluster.reference_point
        nx, ny = cluster.normal

        # Project each segment's endpoints onto the direction axis
        # and compute the spanning interval, checking for overlap/gap
        projections: list[tuple[float, float]] = []
        for seg in segs:
            p_start = _project_onto_axis(
                seg.start[0], seg.start[1], rx, ry, dx, dy
            )
            p_end = _project_onto_axis(
                seg.end[0], seg.end[1], rx, ry, dx, dy
            )
            lo = min(p_start, p_end)
            hi = max(p_start, p_end)
            projections.append((lo, hi))

        # Sort intervals by their lower bound
        projections.sort(key=lambda p: p[0])

        # Merge overlapping/nearby intervals (gap ≤ endpoint_gap)
        merged_intervals: list[tuple[float, float]] = [projections[0]]
        for lo, hi in projections[1:]:
            prev_lo, prev_hi = merged_intervals[-1]
            if lo <= prev_hi + self.endpoint_gap:
                # Overlapping or within gap tolerance → extend
                merged_intervals[-1] = (prev_lo, max(prev_hi, hi))
            else:
                merged_intervals.append((lo, hi))

        # If segments don't form a single continuous span, return only
        # the longest merged interval (architectural primary line)
        best_interval = max(merged_intervals, key=lambda iv: iv[1] - iv[0])
        proj_lo, proj_hi = best_interval

        # Reconstruct the merged line in image coordinates
        # Point on axis: reference_point + projection * direction
        merged_start_x = rx + proj_lo * dx
        merged_start_y = ry + proj_lo * dy
        merged_end_x = rx + proj_hi * dx
        merged_end_y = ry + proj_hi * dy

        # Compute the average perpendicular offset from all segment midpoints
        # to ensure the merged line sits at the "center of mass" of the cluster
        avg_offset = 0.0
        for seg in segs:
            mid_x = (seg.start[0] + seg.end[0]) / 2.0
            mid_y = (seg.start[1] + seg.end[1]) / 2.0
            signed_dist = (mid_x - rx) * nx + (mid_y - ry) * ny
            avg_offset += signed_dist
        avg_offset /= len(segs)

        # Shift merged line to the average offset position
        merged_start_x += avg_offset * nx
        merged_start_y += avg_offset * ny
        merged_end_x += avg_offset * nx
        merged_end_y += avg_offset * ny

        return LineSegment(
            id=str(uuid.uuid4()),
            start=[
                round(merged_start_x, 2),
                round(merged_start_y, 2),
            ],
            end=[
                round(merged_end_x, 2),
                round(merged_end_y, 2),
            ],
        )

    # ──────────────────────────────────────────────────────────────────────
    # Stage 5: Centerline Collapse
    # ──────────────────────────────────────────────────────────────────────

    def _collapse_parallel_pairs(
        self, lines: list[LineSegment]
    ) -> tuple[list[LineSegment], int]:
        """Collapse pairs of parallel lines representing stroke edges.

        Thick drawn strokes in architectural PDFs produce two edge contours
        (inner and outer bounds of the ink thickness).  This stage identifies
        such pairs and replaces them with a single centerline at the midpoint.

        Pairing criteria:
          1. Angle difference < collapse_angle_tol (0.5°).
          2. Perpendicular distance between lines ≤ stroke_thickness (15 px).
          3. Overlap of projected spans ≥ collapse_overlap_ratio (50 %).

        The centerline is the exact midpoint of the paired endpoints:
            center_start = (start_A + start_B) / 2
            center_end   = (end_A   + end_B)   / 2

        For unpaired lines, the original is kept.  In the rare case of
        3+ lines within stroke thickness (e.g., triple edge contours from
        anti-aliasing), they are collapsed greedily in order of closeness.

        Returns:
            (final_lines, pairs_collapsed_count)
        """
        n = len(lines)
        if n < 2:
            return list(lines), 0

        # Pre-compute direction and normal for each line
        line_vecs: list[tuple[list[float], list[float]]] = []
        for line in lines:
            line_vecs.append(_line_from_angle_point(line.angle, line.start[0], line.start[1]))

        # Track which lines have been paired
        paired: set[int] = set()
        pair_map: list[tuple[int, int]] = []  # (i, j) index pairs

        for i in range(n):
            if i in paired:
                continue
            for j in range(i + 1, n):
                if j in paired:
                    continue

                li, lj = lines[i], lines[j]
                di, ni_vec = line_vecs[i]

                # Criterion 1: Angle difference
                if _angular_distance(li.angle, lj.angle) > self.collapse_angle_tol:
                    continue

                # Criterion 2: Perpendicular distance (average of both endpoints)
                rx, ry = li.start
                nx, ny = ni_vec
                perp_dist_start = abs(
                    (lj.start[0] - rx) * nx + (lj.start[1] - ry) * ny
                )
                perp_dist_end = abs(
                    (lj.end[0] - li.end[0]) * nx + (lj.end[1] - li.end[1]) * ny
                )
                avg_perp = (perp_dist_start + perp_dist_end) / 2.0

                if avg_perp > self.stroke_thickness or avg_perp < 1e-6:
                    continue

                # Criterion 3: Projection overlap
                # Project j's endpoints onto i's direction axis
                p_j_start = _project_onto_axis(
                    lj.start[0], lj.start[1], rx, ry, di[0], di[1]
                )
                p_j_end = _project_onto_axis(
                    lj.end[0], lj.end[1], rx, ry, di[0], di[1]
                )
                p_i_start = _project_onto_axis(
                    li.start[0], li.start[1], rx, ry, di[0], di[1]
                )
                p_i_end = _project_onto_axis(
                    li.end[0], li.end[1], rx, ry, di[0], di[1]
                )

                span_i = (min(p_i_start, p_i_end), max(p_i_start, p_i_end))
                span_j = (min(p_j_start, p_j_end), max(p_j_start, p_j_end))

                overlap_lo = max(span_i[0], span_j[0])
                overlap_hi = min(span_i[1], span_j[1])
                overlap_len = max(0.0, overlap_hi - overlap_lo)

                len_i = span_i[1] - span_i[0]
                len_j = span_j[1] - span_j[0]
                min_len = min(len_i, len_j)

                if min_len < 1e-6:
                    continue

                overlap_ratio = overlap_len / min_len
                if overlap_ratio < self.collapse_overlap_ratio:
                    continue

                # Valid pair found
                pair_map.append((i, j))
                paired.add(i)
                paired.add(j)
                break  # i is now paired, move to next unpaired line

        # Build final output: centerlines for pairs + untouched originals
        result: list[LineSegment] = []
        collapsed_ids: set[int] = set()

        for i, j in pair_map:
            li, lj = lines[i], lines[j]
            center = LineSegment(
                id=str(uuid.uuid4()),
                start=[
                    round((li.start[0] + lj.start[0]) / 2.0, 2),
                    round((li.start[1] + lj.start[1]) / 2.0, 2),
                ],
                end=[
                    round((li.end[0] + lj.end[0]) / 2.0, 2),
                    round((li.end[1] + lj.end[1]) / 2.0, 2),
                ],
            )
            result.append(center)
            collapsed_ids.add(i)
            collapsed_ids.add(j)

        # Add unpaired lines as-is
        for idx, line in enumerate(lines):
            if idx not in collapsed_ids:
                result.append(line)

        return result, len(pair_map)

    # ──────────────────────────────────────────────────────────────────────
    # Output Formatting
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _format_output(lines: list[LineSegment]) -> list[dict[str, Any]]:
        """Format consolidated lines as JSON-compatible dicts."""
        return [
            {
                "id": line.id,
                "start": line.start,
                "end": line.end,
                "angle": round(line.angle, 2),
                "length": round(line.length, 2),
            }
            for line in lines
        ]


# ──────────────────────────────────────────────────────────────────────────────
# Convenience Function (single-call API)
# ──────────────────────────────────────────────────────────────────────────────

def consolidate_lines(
    raw_lines: list[dict[str, Any]] | str,
    image_width: int,
    image_height: int,
    *,
    border_threshold: float = BORDER_THRESHOLD,
    angle_tolerance: float = ANGLE_TOLERANCE_DEG,
    perp_distance: float = PERPENDICULAR_DIST_PX,
    endpoint_gap: float = ENDPOINT_GAP_PX,
) -> list[dict[str, Any]]:
    """One-shot consolidation: noisy lines in → clean lines out.

    Args:
        raw_lines:         Noisy line array (JSON string or Python list).
        image_width:       Source image width in pixels.
        image_height:      Source image height in pixels.
        border_threshold:  Border filter threshold (px from edge).
        angle_tolerance:   Max angle difference for parallel (degrees).
        perp_distance:     Max perpendicular distance for collinear (px).
        endpoint_gap:      Max gap between segment spans for merging (px).

    Returns:
        List of consolidated line dicts with 'id', 'start', 'end',
        'angle', and 'length' keys.
    """
    consolidator = LineConsolidator(
        image_width=image_width,
        image_height=image_height,
        border_threshold=border_threshold,
        angle_tolerance_deg=angle_tolerance,
        perp_dist_px=perp_distance,
        endpoint_gap_px=endpoint_gap,
    )
    return consolidator.consolidate(raw_lines)


# ──────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 4:
        print(
            "Usage: python line_consolidator.py <image_width> <image_height> <input.json> [output.json]",
            file=sys.stderr,
        )
        sys.exit(1)

    img_w = int(sys.argv[1])
    img_h = int(sys.argv[2])
    input_path = sys.argv[3]
    output_path = sys.argv[4] if len(sys.argv) > 4 else None

    with open(input_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    result = consolidate_lines(raw, img_w, img_h)

    output_json = json.dumps(result, indent=2)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(output_json)
        print(f"Consolidated {len(raw)} → {len(result)} lines → {output_path}")
    else:
        print(output_json)