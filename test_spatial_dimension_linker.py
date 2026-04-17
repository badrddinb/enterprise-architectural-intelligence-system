#!/usr/bin/env python3
"""
Unit tests for SpatialDimensionLinker — validates text-to-line matching logic.

Tests cover:
  - Geometry helpers (centroid, projection, angle normalization)
  - Textract response parsing (normalized coords, polygon, confidence filtering)
  - Dimension text heuristics
  - Core matching: proximity, angle alignment, midpoint proximity
  - One-to-one matching constraint (no duplicate assignments)
  - Edge cases: empty inputs, degenerate segments, out-of-range text
  - Output schema enrichment (explicit_dimension injection)
  - Convenience function (link_dimensions)
  - Clean output formatting
"""

import json
import math
import sys
import unittest

sys.path.insert(0, "lambda/raster_line_extraction")

from spatial_dimension_linker import (
    DEFAULT_MAX_ANGLE_DIFF,
    DEFAULT_MAX_DISTANCE,
    DEFAULT_MIDPOINT_TOLERANCE,
    TextractBlock,
    SpatialDimensionLinker,
    _LineSegment,
    _angular_distance,
    _centroid_from_bbox,
    _centroid_from_polygon,
    _angle_from_polygon,
    _is_dimension_text,
    _normalize_angle,
    _parse_textract_blocks,
    _point_to_segment_distance,
    link_dimensions,
)


# ──────────────────────────────────────────────────────────────────────────────
# Geometry Helper Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestNormalizeAngle(unittest.TestCase):
    """Tests for _normalize_angle."""

    def test_horizontal_right(self):
        self.assertAlmostEqual(_normalize_angle(0.0), 0.0)

    def test_horizontal_left(self):
        self.assertAlmostEqual(_normalize_angle(180.0), 0.0)

    def test_vertical_down(self):
        self.assertAlmostEqual(_normalize_angle(90.0), 90.0)

    def test_vertical_up(self):
        self.assertAlmostEqual(_normalize_angle(-90.0), 90.0)

    def test_270_maps_to_negative_90_then_90(self):
        self.assertAlmostEqual(_normalize_angle(270.0), 90.0)

    def test_45_stays(self):
        self.assertAlmostEqual(_normalize_angle(45.0), 45.0)

    def test_135_maps_to_negative_45(self):
        self.assertAlmostEqual(_normalize_angle(135.0), -45.0)

    def test_negative_45(self):
        self.assertAlmostEqual(_normalize_angle(-45.0), -45.0)

    def test_360_maps_to_0(self):
        self.assertAlmostEqual(_normalize_angle(360.0), 0.0)


class TestAngularDistance(unittest.TestCase):
    """Tests for _angular_distance."""

    def test_same_angle(self):
        self.assertAlmostEqual(_angular_distance(0.0, 0.0), 0.0)

    def test_perpendicular(self):
        self.assertAlmostEqual(_angular_distance(0.0, 90.0), 90.0)

    def test_small_diff(self):
        self.assertAlmostEqual(_angular_distance(5.0, 8.0), 3.0)

    def test_symmetric(self):
        self.assertAlmostEqual(_angular_distance(10.0, -10.0), 20.0)


class TestPointToSegmentDistance(unittest.TestCase):
    """Tests for _point_to_segment_distance."""

    def test_perpendicular_to_midpoint(self):
        # Horizontal line from (0,0) to (100,0), point at (50, 30)
        dist, t = _point_to_segment_distance(50, 30, 0, 0, 100, 0)
        self.assertAlmostEqual(dist, 30.0)
        self.assertAlmostEqual(t, 0.5)

    def test_perpendicular_near_start(self):
        # Point closer to start
        dist, t = _point_to_segment_distance(10, 20, 0, 0, 100, 0)
        self.assertAlmostEqual(dist, 20.0)
        self.assertAlmostEqual(t, 0.1)

    def test_clamped_to_start(self):
        # Point behind start endpoint
        dist, t = _point_to_segment_distance(-20, 30, 0, 0, 100, 0)
        self.assertAlmostEqual(t, 0.0)
        self.assertAlmostEqual(dist, math.hypot(20, 30))

    def test_clamped_to_end(self):
        # Point beyond end endpoint
        dist, t = _point_to_segment_distance(150, 40, 0, 0, 100, 0)
        self.assertAlmostEqual(t, 1.0)
        self.assertAlmostEqual(dist, math.hypot(50, 40))

    def test_on_segment(self):
        # Point directly on the line
        dist, t = _point_to_segment_distance(50, 0, 0, 0, 100, 0)
        self.assertAlmostEqual(dist, 0.0)
        self.assertAlmostEqual(t, 0.5)

    def test_degenerate_segment(self):
        # Zero-length segment
        dist, t = _point_to_segment_distance(50, 50, 10, 10, 10, 10)
        self.assertAlmostEqual(dist, math.hypot(40, 40))
        self.assertAlmostEqual(t, 0.0)

    def test_diagonal_line(self):
        # 45-degree line from (0,0) to (100,100)
        dist, t = _point_to_segment_distance(50, 0, 0, 0, 100, 100)
        # Perpendicular distance from (50,0) to line y=x
        expected = abs(50 - 0) / math.sqrt(2)
        self.assertAlmostEqual(dist, expected, places=2)


class TestCentroidFromBbox(unittest.TestCase):
    """Tests for _centroid_from_bbox."""

    def test_center_of_unit_bbox(self):
        cx, cy = _centroid_from_bbox(
            {"Left": 0.0, "Top": 0.0, "Width": 1.0, "Height": 1.0},
            img_w=1000, img_h=500,
        )
        self.assertAlmostEqual(cx, 500.0)
        self.assertAlmostEqual(cy, 250.0)

    def test_offset_bbox(self):
        cx, cy = _centroid_from_bbox(
            {"Left": 0.1, "Top": 0.2, "Width": 0.4, "Height": 0.3},
            img_w=1000, img_h=1000,
        )
        self.assertAlmostEqual(cx, 300.0)  # (0.1 + 0.4/2) * 1000
        self.assertAlmostEqual(cy, 350.0)  # (0.2 + 0.3/2) * 1000


class TestCentroidFromPolygon(unittest.TestCase):
    """Tests for _centroid_from_polygon."""

    def test_square_polygon(self):
        polygon = [
            {"X": 0.1, "Y": 0.1},
            {"X": 0.3, "Y": 0.1},
            {"X": 0.3, "Y": 0.3},
            {"X": 0.1, "Y": 0.3},
        ]
        cx, cy = _centroid_from_polygon(polygon, img_w=1000, img_h=1000)
        self.assertAlmostEqual(cx, 200.0)
        self.assertAlmostEqual(cy, 200.0)

    def test_empty_polygon(self):
        cx, cy = _centroid_from_polygon([], img_w=1000, img_h=1000)
        self.assertAlmostEqual(cx, 0.0)
        self.assertAlmostEqual(cy, 0.0)


class TestAngleFromPolygon(unittest.TestCase):
    """Tests for _angle_from_polygon."""

    def test_horizontal_text(self):
        polygon = [
            {"X": 0.1, "Y": 0.2},
            {"X": 0.3, "Y": 0.2},
            {"X": 0.3, "Y": 0.25},
            {"X": 0.1, "Y": 0.25},
        ]
        angle = _angle_from_polygon(polygon, img_w=1000, img_h=1000)
        self.assertAlmostEqual(angle, 0.0)

    def test_vertical_text(self):
        polygon = [
            {"X": 0.2, "Y": 0.1},
            {"X": 0.2, "Y": 0.3},
        ]
        angle = _angle_from_polygon(polygon, img_w=1000, img_h=1000)
        self.assertAlmostEqual(angle, 90.0)

    def test_single_point_returns_none(self):
        polygon = [{"X": 0.5, "Y": 0.5}]
        self.assertIsNone(_angle_from_polygon(polygon, img_w=1000, img_h=1000))

    def test_empty_returns_none(self):
        self.assertIsNone(_angle_from_polygon([], img_w=1000, img_h=1000))


# ──────────────────────────────────────────────────────────────────────────────
# Dimension Text Heuristic Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestIsDimensionText(unittest.TestCase):
    """Tests for _is_dimension_text."""

    def test_feet_inches(self):
        self.assertTrue(_is_dimension_text("15'-0\""))
        self.assertTrue(_is_dimension_text("15'-0"))
        self.assertTrue(_is_dimension_text("15'0\""))

    def test_feet_only(self):
        self.assertTrue(_is_dimension_text("15'"))
        self.assertTrue(_is_dimension_text("20'"))

    def test_inches_only(self):
        self.assertTrue(_is_dimension_text("6\""))
        self.assertTrue(_is_dimension_text("12\""))

    def test_plain_numeric(self):
        self.assertTrue(_is_dimension_text("180"))
        self.assertTrue(_is_dimension_text("4500"))
        self.assertTrue(_is_dimension_text("3.5"))

    def test_with_units(self):
        self.assertTrue(_is_dimension_text("15m"))
        self.assertTrue(_is_dimension_text("450cm"))
        self.assertTrue(_is_dimension_text("1800mm"))

    def test_non_dimension(self):
        self.assertFalse(_is_dimension_text("Wall"))
        self.assertFalse(_is_dimension_text("Section A"))
        self.assertFalse(_is_dimension_text(""))
        self.assertFalse(_is_dimension_text("Note: see detail"))

    def test_fractional(self):
        self.assertTrue(_is_dimension_text("3 1/2\""))
        self.assertTrue(_is_dimension_text("15'-6 1/2\""))


# ──────────────────────────────────────────────────────────────────────────────
# Textract Parsing Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestParseTextractBlocks(unittest.TestCase):
    """Tests for _parse_textract_blocks."""

    def _make_block(
        self,
        text: str = "15'-0\"",
        block_type: str = "WORD",
        confidence: float = 99.0,
        left: float = 0.3,
        top: float = 0.4,
        width: float = 0.05,
        height: float = 0.01,
        with_polygon: bool = True,
    ) -> dict:
        """Helper to create a Textract block dict."""
        block = {
            "BlockType": block_type,
            "Text": text,
            "Confidence": confidence,
            "Id": f"test-block-{text}",
            "Geometry": {
                "BoundingBox": {
                    "Left": left,
                    "Top": top,
                    "Width": width,
                    "Height": height,
                },
            },
        }
        if with_polygon:
            block["Geometry"]["Polygon"] = [
                {"X": left, "Y": top},
                {"X": left + width, "Y": top},
                {"X": left + width, "Y": top + height},
                {"X": left, "Y": top + height},
            ]
        return block

    def test_parses_word_block(self):
        blocks = [self._make_block("15'-0\"", "WORD")]
        result = _parse_textract_blocks(blocks, 1000, 1000, filter_dimensions=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "15'-0\"")
        self.assertAlmostEqual(result[0].confidence, 99.0)

    def test_filters_line_block_type(self):
        blocks = [self._make_block("15'-0\"", "LINE")]
        result = _parse_textract_blocks(blocks, 1000, 1000, filter_dimensions=True)
        self.assertEqual(len(result), 1)

    def test_ignores_page_block_type(self):
        blocks = [self._make_block("page-text", "PAGE")]
        result = _parse_textract_blocks(blocks, 1000, 1000, filter_dimensions=True)
        self.assertEqual(len(result), 0)

    def test_filters_low_confidence(self):
        blocks = [self._make_block("15'-0\"", confidence=30.0)]
        result = _parse_textract_blocks(blocks, 1000, 1000, confidence_threshold=50.0)
        self.assertEqual(len(result), 0)

    def test_filters_non_dimension_text(self):
        blocks = [self._make_block("Wall Section", "WORD", confidence=99.0)]
        result = _parse_textract_blocks(blocks, 1000, 1000, filter_dimensions=True)
        self.assertEqual(len(result), 0)

    def test_no_dimension_filter(self):
        blocks = [self._make_block("Wall Section", "WORD", confidence=99.0)]
        result = _parse_textract_blocks(blocks, 1000, 1000, filter_dimensions=False)
        self.assertEqual(len(result), 1)

    def test_centroid_from_polygon(self):
        blocks = [self._make_block("100", left=0.1, top=0.2, width=0.1, height=0.05)]
        result = _parse_textract_blocks(blocks, 1000, 1000, filter_dimensions=True)
        self.assertEqual(len(result), 1)
        # Centroid of polygon: avg of 4 vertices
        # (0.1,0.2), (0.2,0.2), (0.2,0.25), (0.1,0.25) → centroid (0.15, 0.225)
        self.assertAlmostEqual(result[0].cx, 150.0)
        self.assertAlmostEqual(result[0].cy, 225.0)

    def test_centroid_from_bbox_fallback(self):
        block = {
            "BlockType": "WORD",
            "Text": "100",
            "Confidence": 99.0,
            "Id": "test-bbox",
            "Geometry": {
                "BoundingBox": {
                    "Left": 0.1, "Top": 0.2, "Width": 0.1, "Height": 0.05,
                },
            },
        }
        result = _parse_textract_blocks([block], 1000, 1000, filter_dimensions=True)
        self.assertEqual(len(result), 1)
        # Centroid from bbox: (0.1+0.1/2)*1000=150, (0.2+0.05/2)*1000=225
        self.assertAlmostEqual(result[0].cx, 150.0)
        self.assertAlmostEqual(result[0].cy, 225.0)

    def test_empty_text_skipped(self):
        blocks = [self._make_block("", "WORD")]
        result = _parse_textract_blocks(blocks, 1000, 1000, filter_dimensions=True)
        self.assertEqual(len(result), 0)


# ──────────────────────────────────────────────────────────────────────────────
# Core Linker Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestSpatialDimensionLinker(unittest.TestCase):
    """Tests for SpatialDimensionLinker.link() and related methods."""

    def _make_line(
        self, x1: float, y1: float, x2: float, y2: float, line_id: str = None,
    ) -> dict:
        return {
            "id": line_id or f"line-{x1}-{y1}-{x2}-{y2}",
            "start": [x1, y1],
            "end": [x2, y2],
            "angle": round(math.degrees(math.atan2(y2 - y1, x2 - x1)), 2),
            "length": round(math.hypot(x2 - x1, y2 - y1), 2),
        }

    def _make_text(
        self, text: str, cx: float, cy: float,
        angle: float = None, confidence: float = 99.0,
    ) -> TextractBlock:
        return TextractBlock(
            block_id=f"block-{text}",
            text=text,
            confidence=confidence,
            cx=cx,
            cy=cy,
            angle=angle,
        )

    # ── Basic Matching ────────────────────────────────────────────────────

    def test_text_at_line_midpoint_matches(self):
        """Text directly above a horizontal line's midpoint should match."""
        lines = [self._make_line(100, 200, 500, 200)]  # horizontal line
        # Text centered at midpoint (300, 180) — 20px above
        texts = [self._make_text("15'-0\"", 300, 180, angle=0.0)]

        linker = SpatialDimensionLinker(max_distance=50.0)
        result = linker.link(lines, texts)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["explicit_dimension"], "15'-0\"")
        self.assertAlmostEqual(result[0]["_match_perp_distance"], 20.0, places=1)

    def test_text_far_from_line_no_match(self):
        """Text beyond max_distance should not match."""
        lines = [self._make_line(100, 200, 500, 200)]
        texts = [self._make_text("15'-0\"", 300, 100, angle=0.0)]  # 100px away

        linker = SpatialDimensionLinker(max_distance=50.0)
        result = linker.link(lines, texts)

        self.assertEqual(len(result), 1)
        self.assertNotIn("explicit_dimension", result[0])

    def test_text_near_endpoint_no_match_midpoint_rule(self):
        """Text near an endpoint (far from midpoint) should not match."""
        lines = [self._make_line(0, 200, 1000, 200)]  # long line
        texts = [self._make_text("15'-0\"", 50, 180, angle=0.0)]  # near start

        linker = SpatialDimensionLinker(
            max_distance=50.0, midpoint_tolerance=0.3,
        )
        result = linker.link(lines, texts)

        self.assertEqual(len(result), 1)
        self.assertNotIn("explicit_dimension", result[0])

    # ── Angle Alignment ───────────────────────────────────────────────────

    def test_parallel_text_matches(self):
        """Text with same angle as line should match."""
        # Line at 0° (horizontal)
        lines = [self._make_line(100, 200, 500, 200)]
        # Text at 0° (horizontal) near midpoint
        texts = [self._make_text("15'-0\"", 300, 180, angle=0.0)]

        linker = SpatialDimensionLinker(max_distance=50.0, max_angle_diff=15.0)
        result = linker.link(lines, texts)

        self.assertEqual(result[0].get("explicit_dimension"), "15'-0\"")

    def test_orthogonal_text_matches(self):
        """Text at 90° to the line should also match (perpendicular dimension)."""
        # Horizontal line
        lines = [self._make_line(100, 200, 500, 200)]
        # Vertical text (90°) near midpoint
        texts = [self._make_text("15'-0\"", 300, 180, angle=90.0)]

        linker = SpatialDimensionLinker(max_distance=50.0, max_angle_diff=15.0)
        result = linker.link(lines, texts)

        self.assertEqual(result[0].get("explicit_dimension"), "15'-0\"")

    def test_oblique_text_rejected(self):
        """Text at 45° to a horizontal line should not match (outside tolerance)."""
        lines = [self._make_line(100, 200, 500, 200)]
        texts = [self._make_text("15'-0\"", 300, 180, angle=45.0)]

        linker = SpatialDimensionLinker(max_distance=50.0, max_angle_diff=15.0)
        result = linker.link(lines, texts)

        self.assertNotIn("explicit_dimension", result[0])

    def test_no_angle_info_skips_check(self):
        """Text without angle info should match based on distance only."""
        lines = [self._make_line(100, 200, 500, 200)]
        texts = [self._make_text("15'-0\"", 300, 180, angle=None)]

        linker = SpatialDimensionLinker(max_distance=50.0)
        result = linker.link(lines, texts)

        self.assertEqual(result[0].get("explicit_dimension"), "15'-0\"")

    # ── One-to-One Constraint ─────────────────────────────────────────────

    def test_one_to_one_matching(self):
        """Two texts near the same line: only the best match wins."""
        lines = [self._make_line(100, 200, 500, 200)]
        texts = [
            self._make_text("15'-0\"", 300, 190, angle=0.0),  # 10px away
            self._make_text("20'-0\"", 300, 180, angle=0.0),  # 20px away
        ]

        linker = SpatialDimensionLinker(max_distance=50.0)
        result = linker.link(lines, texts)

        # Only one match; the closer text wins
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["explicit_dimension"], "15'-0\"")

    def test_two_lines_two_texts_correct_pairing(self):
        """Each text matches its closest line (one-to-one)."""
        lines = [
            self._make_line(100, 100, 500, 100, "line-top"),
            self._make_line(100, 400, 500, 400, "line-bottom"),
        ]
        texts = [
            self._make_text("10'-0\"", 300, 80, angle=0.0),   # near top line
            self._make_text("20'-0\"", 300, 380, angle=0.0),  # near bottom line
        ]

        linker = SpatialDimensionLinker(max_distance=50.0)
        result = linker.link(lines, texts)

        top_line = next(r for r in result if r["id"] == "line-top")
        bottom_line = next(r for r in result if r["id"] == "line-bottom")

        self.assertEqual(top_line["explicit_dimension"], "10'-0\"")
        self.assertEqual(bottom_line["explicit_dimension"], "20'-0\"")

    # ── Multiple Lines ────────────────────────────────────────────────────

    def test_multiple_lines_single_text_matches_best(self):
        """Text matches the closest of multiple candidate lines."""
        lines = [
            self._make_line(100, 100, 500, 100, "line-far"),
            self._make_line(100, 200, 500, 200, "line-near"),
        ]
        texts = [self._make_text("15'-0\"", 300, 180, angle=0.0)]

        linker = SpatialDimensionLinker(max_distance=50.0)
        result = linker.link(lines, texts)

        near_line = next(r for r in result if r["id"] == "line-near")
        far_line = next(r for r in result if r["id"] == "line-far")

        self.assertEqual(near_line["explicit_dimension"], "15'-0\"")
        self.assertNotIn("explicit_dimension", far_line)

    # ── Vertical Lines ────────────────────────────────────────────────────

    def test_vertical_line_with_horizontal_text(self):
        """Horizontal text next to a vertical line should match (orthogonal case)."""
        lines = [self._make_line(200, 100, 200, 500)]  # vertical line
        # Text at (220, 300) — 20px to the right, horizontal
        texts = [self._make_text("15'-0\"", 220, 300, angle=0.0)]

        linker = SpatialDimensionLinker(max_distance=50.0)
        result = linker.link(lines, texts)

        self.assertEqual(result[0].get("explicit_dimension"), "15'-0\"")

    # ── Edge Cases ────────────────────────────────────────────────────────

    def test_empty_lines(self):
        linker = SpatialDimensionLinker()
        result = linker.link([], [self._make_text("15'-0\"", 300, 180)])
        self.assertEqual(result, [])
        self.assertEqual(linker.get_stats()["matches"], 0)

    def test_empty_texts(self):
        lines = [self._make_line(100, 200, 500, 200)]
        linker = SpatialDimensionLinker()
        result = linker.link(lines, [])
        self.assertEqual(len(result), 1)
        self.assertNotIn("explicit_dimension", result[0])

    def test_both_empty(self):
        linker = SpatialDimensionLinker()
        result = linker.link([], [])
        self.assertEqual(result, [])

    # ── Output Schema ─────────────────────────────────────────────────────

    def test_output_schema_enrichment(self):
        """Verify the enriched output matches the expected schema."""
        lines = [self._make_line(100, 200, 500, 200, "test-line-id")]
        texts = [self._make_text("15'-0\"", 300, 180, angle=0.0)]

        linker = SpatialDimensionLinker(max_distance=50.0)
        result = linker.link(lines, texts)

        line = result[0]
        self.assertEqual(line["id"], "test-line-id")
        self.assertIsInstance(line["start"], list)
        self.assertIsInstance(line["end"], list)
        self.assertEqual(line["explicit_dimension"], "15'-0\"")
        self.assertIn("dimension_confidence", line)
        self.assertIn("dimension_source_block_id", line)
        self.assertEqual(line["dimension_confidence"], 0.99)

    def test_output_carries_original_fields(self):
        """Original fields like angle and length are preserved."""
        lines = [{
            "id": "test-1",
            "start": [100, 200],
            "end": [500, 200],
            "angle": 0.0,
            "length": 400.0,
        }]
        texts = [self._make_text("15'-0\"", 300, 180, angle=0.0)]

        linker = SpatialDimensionLinker(max_distance=50.0)
        result = linker.link(lines, texts)

        self.assertEqual(result[0]["angle"], 0.0)
        self.assertEqual(result[0]["length"], 400.0)

    # ── Statistics ────────────────────────────────────────────────────────

    def test_stats_reported(self):
        lines = [self._make_line(100, 200, 500, 200)]
        texts = [
            self._make_text("15'-0\"", 300, 180, angle=0.0),
            self._make_text("unmatched", 1000, 1000, angle=0.0, confidence=99.0),
        ]

        linker = SpatialDimensionLinker(
            max_distance=50.0, confidence_threshold=1.0, filter_dimensions=False,
        )
        result = linker.link(lines, texts)
        stats = linker.get_stats()

        self.assertEqual(stats["input_lines"], 1)
        self.assertEqual(stats["text_blocks"], 2)
        self.assertEqual(stats["matches"], 1)
        self.assertEqual(stats["unmatched_lines"], 0)
        self.assertEqual(stats["unmatched_texts"], 1)

    # ── Alternative Input Format ──────────────────────────────────────────

    def test_x1y1x2y2_format(self):
        """Lines using x1,y1,x2,y2 format should work."""
        lines = [{"x1": 100, "y1": 200, "x2": 500, "y2": 200, "id": "alt-format"}]
        texts = [self._make_text("15'-0\"", 300, 180, angle=0.0)]

        linker = SpatialDimensionLinker(max_distance=50.0)
        result = linker.link(lines, texts)

        self.assertEqual(result[0]["explicit_dimension"], "15'-0\"")


# ──────────────────────────────────────────────────────────────────────────────
# Full Textract Response Integration Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestLinkFromTextractResponse(unittest.TestCase):
    """Tests for SpatialDimensionLinker.link_from_textract_response()."""

    def _make_textract_response(
        self,
        text: str = "15'-0\"",
        confidence: float = 99.0,
        left: float = 0.29,
        top: float = 0.19,
        width: float = 0.02,
        height: float = 0.005,
    ) -> dict:
        """Create a minimal Textract API response."""
        return {
            "Blocks": [
                {
                    "BlockType": "WORD",
                    "Text": text,
                    "Confidence": confidence,
                    "Id": f"block-{text}",
                    "Geometry": {
                        "BoundingBox": {
                            "Left": left,
                            "Top": top,
                            "Width": width,
                            "Height": height,
                        },
                        "Polygon": [
                            {"X": left, "Y": top},
                            {"X": left + width, "Y": top},
                            {"X": left + width, "Y": top + height},
                            {"X": left, "Y": top + height},
                        ],
                    },
                },
                {
                    "BlockType": "PAGE",
                    "Text": None,
                    "Confidence": 0.0,
                },
            ],
        }

    def test_full_pipeline_match(self):
        """End-to-end: Textract response + lines → enriched output."""
        # Image is 1000x1000; line from (100,200) to (500,200)
        # Text bbox centered at left+width/2 = 0.29+0.01=0.30 → x=300
        #                 top+height/2 = 0.19+0.0025 ≈ 0.1925 → y=192.5
        lines = [{"id": "L1", "start": [100, 200], "end": [500, 200]}]
        textract = self._make_textract_response(
            text="15'-0\"", left=0.29, top=0.185, width=0.02, height=0.01,
        )

        linker = SpatialDimensionLinker(max_distance=50.0)
        result = linker.link_from_textract_response(
            lines=lines,
            textract_response=textract,
            image_width=1000,
            image_height=1000,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["explicit_dimension"], "15'-0\"")

    def test_full_pipeline_no_match(self):
        """No match when text is too far from any line."""
        lines = [{"id": "L1", "start": [100, 200], "end": [500, 200]}]
        textract = self._make_textract_response(
            text="15'-0\"", left=0.8, top=0.8,  # far away
        )

        linker = SpatialDimensionLinker(max_distance=50.0)
        result = linker.link_from_textract_response(
            lines=lines,
            textract_response=textract,
            image_width=1000,
            image_height=1000,
        )

        self.assertEqual(len(result), 1)
        self.assertNotIn("explicit_dimension", result[0])


# ──────────────────────────────────────────────────────────────────────────────
# Convenience Function Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestLinkDimensions(unittest.TestCase):
    """Tests for the link_dimensions() convenience function."""

    def test_convenience_function(self):
        lines = [{"id": "L1", "start": [100, 200], "end": [500, 200]}]
        textract = {
            "Blocks": [
                {
                    "BlockType": "WORD",
                    "Text": "100",
                    "Confidence": 99.0,
                    "Id": "b1",
                    "Geometry": {
                        "BoundingBox": {
                            "Left": 0.29, "Top": 0.18,
                            "Width": 0.02, "Height": 0.01,
                        },
                        "Polygon": [
                            {"X": 0.29, "Y": 0.18},
                            {"X": 0.31, "Y": 0.18},
                            {"X": 0.31, "Y": 0.19},
                            {"X": 0.29, "Y": 0.19},
                        ],
                    },
                },
            ],
        }

        result = link_dimensions(
            lines=lines,
            textract_response=textract,
            image_width=1000,
            image_height=1000,
            max_distance=50.0,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["explicit_dimension"], "100")


# ──────────────────────────────────────────────────────────────────────────────
# Clean Output Formatting Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestFormatClean(unittest.TestCase):
    """Tests for SpatialDimensionLinker.format_clean()."""

    def test_strips_debug_fields(self):
        enriched = [
            {
                "id": "L1",
                "start": [100, 200],
                "end": [500, 200],
                "explicit_dimension": "15'-0\"",
                "_match_score": 0.85,
                "_match_perp_distance": 10.0,
                "_match_angle_diff": 0.0,
                "_match_midpoint_offset": 0.05,
            }
        ]

        clean = SpatialDimensionLinker.format_clean(enriched)
        self.assertEqual(len(clean), 1)
        self.assertIn("explicit_dimension", clean[0])
        self.assertNotIn("_match_score", clean[0])
        self.assertNotIn("_match_perp_distance", clean[0])

    def test_keeps_debug_when_requested(self):
        enriched = [
            {
                "id": "L1",
                "start": [100, 200],
                "end": [500, 200],
                "_match_score": 0.85,
            }
        ]

        kept = SpatialDimensionLinker.format_clean(enriched, include_debug=True)
        self.assertIn("_match_score", kept[0])


# ──────────────────────────────────────────────────────────────────────────────
# LineSegment Internal Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestLineSegment(unittest.TestCase):
    """Tests for _LineSegment internal dataclass."""

    def test_horizontal_line(self):
        seg = _LineSegment(id="test", start=[0, 0], end=[100, 0])
        self.assertAlmostEqual(seg.length, 100.0)
        self.assertAlmostEqual(seg.angle, 0.0)
        self.assertAlmostEqual(seg.midx, 50.0)
        self.assertAlmostEqual(seg.midy, 0.0)

    def test_vertical_line(self):
        seg = _LineSegment(id="test", start=[0, 0], end=[0, 100])
        self.assertAlmostEqual(seg.length, 100.0)
        self.assertAlmostEqual(seg.angle, 90.0)

    def test_diagonal_line(self):
        seg = _LineSegment(id="test", start=[0, 0], end=[100, 100])
        self.assertAlmostEqual(seg.length, math.sqrt(2) * 100, places=2)
        self.assertAlmostEqual(seg.angle, 45.0)

    def test_direction_and_normal(self):
        seg = _LineSegment(id="test", start=[0, 0], end=[100, 0])
        self.assertAlmostEqual(seg.dx, 1.0)
        self.assertAlmostEqual(seg.dy, 0.0)
        self.assertAlmostEqual(seg.nx, 0.0)
        self.assertAlmostEqual(seg.ny, 1.0)


# ──────────────────────────────────────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)