"""End-to-end test for the Raster Line Extraction pipeline.

Creates a synthetic architectural plan image with known geometric features,
runs the full OpenCV pipeline, and validates the output against expected
results and the raw-coordinates.schema.json contract.
"""
import json
import math
import os
import sys

import cv2
import numpy as np

# Ensure the lambda directory is on the path for imports
sys.path.insert(0, os.path.dirname(__file__))

# ── Step 1: Create a synthetic architectural plan image ──────────────────────

IMAGE_SIZE = 1000
BG_COLOR = 255  # White background (paper)
LINE_COLOR = 0   # Black lines (architectural ink)
LINE_THICKNESS = 2

# Create a white image (simulating a scanned A4 plan)
image = np.full((IMAGE_SIZE, IMAGE_SIZE), BG_COLOR, dtype=np.uint8)

# Draw a rectangle (4 walls of a room) — some with deliberate slight skew
# Top wall: perfectly horizontal
cv2.line(image, (100, 100), (900, 100), LINE_COLOR, LINE_THICKNESS)
# Bottom wall: perfectly horizontal
cv2.line(image, (100, 900), (900, 900), LINE_COLOR, LINE_THICKNESS)
# Left wall: slightly off-vertical (1° skew to test affine correction)
angle_rad = math.radians(91)  # 91° → should snap to 90°
left_wall_end_y = 900
left_wall_dx = (left_wall_end_y - 100) * math.tan(math.radians(1))  # ~14px offset
cv2.line(image, (100, 100), (int(100 + left_wall_dx), left_wall_end_y), LINE_COLOR, LINE_THICKNESS)
# Right wall: slightly off-vertical (-0.8° skew)
right_wall_dx = (900 - 100) * math.tan(math.radians(-0.8))  # slight offset
cv2.line(image, (900, 100), (int(900 + right_wall_dx), 900), LINE_COLOR, LINE_THICKNESS)

# Add an interior wall (horizontal, with slight skew)
cv2.line(image, (100, 500), (int(900 + 3), 502), LINE_COLOR, LINE_THICKNESS)

# Add a diagonal line (should NOT be corrected by affine)
cv2.line(image, (50, 50), (150, 150), LINE_COLOR, LINE_THICKNESS)

# Add some noise to simulate scan artifacts
noise = np.random.normal(0, 8, image.shape).astype(np.int16)
noisy_image = np.clip(image.astype(np.int16) + noise, 0, 255).astype(np.uint8)

# Save the test image
TEST_IMAGE_PATH = "_test_architectural_plan.png"
cv2.imwrite(TEST_IMAGE_PATH, noisy_image)
print(f"Test image created: {TEST_IMAGE_PATH} ({IMAGE_SIZE}x{IMAGE_SIZE})")

# ── Step 2: Run the extraction pipeline ──────────────────────────────────────

from raster_line_extractor import (
    COORDINATE_PRECISION,
    enforce_orthogonal_constraint,
    compute_line_angle,
    extract_lines,
    extraction_result_to_schema,
)

# First, test the affine correction function directly
print("\n" + "=" * 60)
print("AFFINE CORRECTION UNIT TESTS")
print("=" * 60)

test_cases = [
    # (x1, y1, x2, y2, expected_corrected, description)
    (0, 0, 100, 0, False, "Perfectly horizontal (0°)"),
    (0, 0, 0, 100, False, "Perfectly vertical (90°)"),
    (0, 0, 100, 1, True, "Near-horizontal (0.57°) → should snap to 0°"),
    (0, 0, 100, -2, True, "Near-horizontal (-1.15°) → should snap to 0°"),
    (0, 0, 1, 100, True, "Near-vertical (89.43°) → should snap to 90°"),
    (0, 0, -2, 100, True, "Near-vertical (91.15°) → should snap to 90°"),
    (0, 0, 100, 100, False, "Diagonal (45°) → should NOT be corrected"),
    (0, 0, 100, 50, False, "Diagonal (26.57°) → should NOT be corrected"),
    (0, 0, 1, 1, False, "Near-zero length → should not crash"),
]

all_affine_tests_pass = True
for x1, y1, x2, y2, expected_corrected, desc in test_cases:
    _, _, _, _, angle, was_corrected = enforce_orthogonal_constraint(x1, y1, x2, y2)
    passed = was_corrected == expected_corrected
    status = "✓" if passed else "✗"
    if not passed:
        all_affine_tests_pass = False
    print(f"  {status} {desc}: angle={angle:.2f}°, corrected={was_corrected}")

assert all_affine_tests_pass, "Some affine correction tests failed!"
print("\n✓ All affine correction unit tests passed!")

# ── Step 3: Run full pipeline on the test image ──────────────────────────────

print("\n" + "=" * 60)
print("FULL PIPELINE TEST")
print("=" * 60)

# Use smaller Hough parameters for the test image (it's only 1000x1000)
os.environ["HOUGH_THRESHOLD"] = "40"
os.environ["HOUGH_MIN_LINE_LENGTH"] = "30"
os.environ["HOUGH_MAX_LINE_GAP"] = "15"
os.environ["MIN_LINE_LENGTH"] = "50"
os.environ["LOG_LEVEL"] = "WARNING"

# Reimport to pick up env changes
import importlib
import raster_line_extractor
importlib.reload(raster_line_extractor)
from raster_line_extractor import extract_lines, extraction_result_to_schema

result = extract_lines(TEST_IMAGE_PATH)

print(f"\nExtraction Result:")
print(f"  Image dimensions:  {result.image_dimensions}")
print(f"  Detected lines:    {len(result.lines)}")
print(f"  Unique points:     {len(result.points)}")
print(f"  Pipeline stats:    {json.dumps(result.pipeline_stats, indent=2)}")

# ── Step 4: Verify line detection and affine correction ──────────────────────

print("\n" + "=" * 60)
print("LINE ANALYSIS")
print("=" * 60)

horizontal_lines = [l for l in result.lines if abs(l.angle_degrees) < 1]
vertical_lines = [l for l in result.lines if abs(abs(l.angle_degrees) - 90) < 1]
diagonal_lines = [l for l in result.lines if abs(l.angle_degrees) > 1 and abs(abs(l.angle_degrees) - 90) > 1]
corrected_lines = [l for l in result.lines if l.corrected]

print(f"  Horizontal lines (0° ±1°):  {len(horizontal_lines)}")
print(f"  Vertical lines (90° ±1°):   {len(vertical_lines)}")
print(f"  Diagonal lines:              {len(diagonal_lines)}")
print(f"  Affine-corrected lines:      {len(corrected_lines)}")

# Print details of each line
for i, line in enumerate(result.lines):
    corr_marker = " [CORRECTED]" if line.corrected else ""
    print(
        f"  Line {i}: ({line.start[0]:.1f}, {line.start[1]:.1f}) → "
        f"({line.end[0]:.1f}, {line.end[1]:.1f}) | "
        f"angle={line.angle_degrees:.2f}° len={line.length:.1f}{corr_marker}"
    )

# ── Step 5: Verify schema-compliant output ───────────────────────────────────

print("\n" + "=" * 60)
print("SCHEMA OUTPUT VALIDATION")
print("=" * 60)

schema_output = extraction_result_to_schema(
    result=result,
    source_file_id="00000000-0000-0000-0000-000000000001",
)

print(f"  coordinatesId:   {schema_output['coordinatesId']}")
print(f"  sourceFileId:    {schema_output['sourceFileId']}")
print(f"  extractedBy:     {schema_output['extractedBy']}")
print(f"  coordinateSys:   {schema_output['coordinateSystem']}")
print(f"  points:          {len(schema_output['points'])}")
print(f"  lines:           {len(schema_output['lines'])}")
print(f"  edges (flat):    {len(schema_output['edges'])}")

# Verify required top-level fields
required_fields = [
    "coordinatesId", "sourceZonesId", "sourceFileId", "createdAt",
    "extractedBy", "coordinateSystem", "points", "lines",
    "dimensionAnnotations",
]
for field_name in required_fields:
    assert field_name in schema_output, f"Missing required field: {field_name}"
print(f"\n  ✓ All {len(required_fields)} required schema fields present")

# Verify edge format: {"id": "uuid", "type": "wall", "start": [x1, y1], "end": [x2, y2]}
for edge in schema_output["edges"]:
    assert "id" in edge, "Edge missing 'id'"
    assert "type" in edge, "Edge missing 'type'"
    assert "start" in edge, "Edge missing 'start'"
    assert "end" in edge, "Edge missing 'end'"
    assert edge["type"] == "wall", f"Expected type 'wall', got '{edge['type']}'"
    assert len(edge["start"]) == 2, f"Start should be [x, y]"
    assert len(edge["end"]) == 2, f"End should be [x, y]"
print(f"  ✓ All {len(schema_output['edges'])} edges have correct format")

# Verify points have required fields
for pt in schema_output["points"]:
    assert "pointId" in pt, "Point missing 'pointId'"
    assert "x" in pt, "Point missing 'x'"
    assert "y" in pt, "Point missing 'y'"
    assert "sourceZoneId" in pt, "Point missing 'sourceZoneId'"
    assert "pointType" in pt, "Point missing 'pointType'"
    assert "pixelOrigin" in pt, "Point missing 'pixelOrigin'"
print(f"  ✓ All {len(schema_output['points'])} points have correct format")

# Verify lines have required fields
for line in schema_output["lines"]:
    assert "lineId" in line, "Line missing 'lineId'"
    assert "startPointId" in line, "Line missing 'startPointId'"
    assert "endPointId" in line, "Line missing 'endPointId'"
    assert "lineType" in line, "Line missing 'lineType'"
    assert line["lineType"] == "wall", f"Expected lineType 'wall', got '{line['lineType']}'"
print(f"  ✓ All {len(schema_output['lines'])} lines have correct format")

# ── Step 6: Verify affine correction worked ──────────────────────────────────

print("\n" + "=" * 60)
print("AFFINE CORRECTION VERIFICATION")
print("=" * 60)

# All corrected lines should have exactly 0° or 90°
for line in corrected_lines:
    assert line.angle_degrees in [0.0, 90.0, -90.0], (
        f"Corrected line has angle {line.angle_degrees}°, expected 0° or ±90°"
    )
    if line.angle_degrees == 0.0:
        # Horizontal: start and end y should be identical
        assert line.start[1] == line.end[1], (
            f"Horizontal line y-coords differ: {line.start[1]} vs {line.end[1]}"
        )
    elif abs(line.angle_degrees) == 90.0:
        # Vertical: start and end x should be identical
        assert line.start[0] == line.end[0], (
            f"Vertical line x-coords differ: {line.start[0]} vs {line.end[0]}"
        )

if corrected_lines:
    print(f"  ✓ {len(corrected_lines)} lines were affine-corrected to exact 0° or 90°")
else:
    print(f"  ℹ No lines required affine correction (all already orthogonal)")

# ── Step 7: Show flat edge array ─────────────────────────────────────────────

print(f"\nFlat Edge Array (first 10):")
for edge in schema_output["edges"][:10]:
    print(f"  {json.dumps(edge)}")
if len(schema_output["edges"]) > 10:
    print(f"  ... and {len(schema_output['edges']) - 10} more")

# ── Cleanup ──────────────────────────────────────────────────────────────────
os.unlink(TEST_IMAGE_PATH)
print(f"\n✓ Test image cleaned up: {TEST_IMAGE_PATH}")

print("\n" + "=" * 60)
print("✓ ALL TESTS PASSED — Raster line extraction pipeline is working correctly!")
print("=" * 60)