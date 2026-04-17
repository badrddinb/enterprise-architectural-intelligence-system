"""End-to-end test for the DXF geometry extraction pipeline."""
import ezdxf
import json
import sys

# ── Step 1: Create a test DXF ──────────────────────────────────────────────
doc = ezdxf.new("R2010")
msp = doc.modelspace()

# Add 4 lines forming a rectangle (shared corners)
msp.add_line(start=(0, 0), end=(10, 0))
msp.add_line(start=(10, 0), end=(10, 5))
msp.add_line(start=(10, 5), end=(0, 5))
msp.add_line(start=(0, 5), end=(0, 0))

# Add a closed LWPOLYLINE (triangle)
msp.add_lwpolyline(
    [(20, 0), (30, 0), (25, 8)],
    format="xy",
    dxfattribs={"closed": True},
)

# Add MTEXT with a dimension value
msp.add_mtext("5000 mm", dxfattribs={"insert": (5, 2.5)})
msp.add_mtext("Room label", dxfattribs={"insert": (25, 4)})

doc.saveas("_test_sample.dxf")
print("Test DXF created: _test_sample.dxf")

# ── Step 2: Extract geometry ───────────────────────────────────────────────
from geometry_extractor import extract_geometry, extraction_result_to_schema

result = extract_geometry("_test_sample.dxf")

print(f"\nExtraction Result:")
print(f"  Unique points:     {len(result.points)}")
print(f"  Total edges:       {len(result.edges)}")
print(f"  Flat edges:        {len(result.edges_flat)}")
print(f"  Annotations:       {len(result.annotations)}")
print(f"  Entity counts:     {result.entity_counts}")

# ── Step 3: Show flat edge array ───────────────────────────────────────────
print(f"\nFlat Edge Array (task-specified output):")
for edge in result.edges_flat:
    print(f"  {json.dumps(edge)}")

# ── Step 4: Convert to schema format ──────────────────────────────────────
schema_output = extraction_result_to_schema(
    result=result,
    source_file_id="00000000-0000-0000-0000-000000000001",
    coordinate_units="meters",
)

print(f"\nSchema-Compliant Output:")
print(f"  coordinatesId:  {schema_output['coordinatesId']}")
print(f"  sourceFileId:   {schema_output['sourceFileId']}")
print(f"  points:         {len(schema_output['points'])}")
print(f"  lines:          {len(schema_output['lines'])}")
print(f"  annotations:    {len(schema_output['dimensionAnnotations'])}")
print(f"  edges (flat):   {len(schema_output['edges'])}")
print(f"  coordinateSys:  {schema_output['coordinateSystem']}")

# ── Step 5: Validate structure ─────────────────────────────────────────────
assert len(result.points) == 7, f"Expected 7 unique points (4 rect corners + 3 triangle), got {len(result.points)}"
assert len(result.edges) == 7, f"Expected 7 edges (4 rect + 3 triangle), got {len(result.edges)}"
assert len(result.annotations) == 2, f"Expected 2 annotations, got {len(result.annotations)}"

# Verify edge format
for edge in result.edges_flat:
    assert "id" in edge, "Edge missing 'id'"
    assert "type" in edge, "Edge missing 'type'"
    assert "start" in edge, "Edge missing 'start'"
    assert "end" in edge, "Edge missing 'end'"
    assert edge["type"] == "wall", f"Expected type 'wall', got '{edge['type']}'"
    assert len(edge["start"]) == 2, f"Start should be [x, y]"
    assert len(edge["end"]) == 2, f"End should be [x, y]"

# Verify dimension annotation parsed
dim_annotations = [a for a in result.annotations if a.numeric_value is not None]
assert len(dim_annotations) == 1, f"Expected 1 dimension annotation, got {len(dim_annotations)}"
assert dim_annotations[0].numeric_value == 5000.0, f"Expected 5000.0, got {dim_annotations[0].numeric_value}"
assert dim_annotations[0].unit == "mm", f"Expected 'mm', got {dim_annotations[0].unit}"

print("\n✓ All assertions passed — geometry extraction is working correctly!")

# Cleanup
import os
os.unlink("_test_sample.dxf")
print("✓ Test DXF cleaned up")