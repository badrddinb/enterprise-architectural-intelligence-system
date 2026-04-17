#!/usr/bin/env python3
"""Test the LineConsolidator with real raster extraction output."""

import json
import sys

sys.path.insert(0, "lambda/raster_line_extraction")

from raster_line_extractor import extract_lines
from line_consolidator import LineConsolidator, consolidate_lines

# ── Step 1: Run the existing raster pipeline on test_line.pdf ────────────────
print("=" * 70)
print("STEP 1: Raster Extraction Pipeline (test_line.pdf)")
print("=" * 70)

result = extract_lines("test_line.pdf")
print(f"  Image dimensions: {result.image_dimensions}")
print(f"  Raw lines detected: {len(result.lines)}")

# Build the noisy input for the consolidator
noisy_lines = []
for line in result.lines:
    noisy_lines.append({
        "id": line.id,
        "start": line.start,
        "end": line.end,
        "angle": line.angle_degrees,
        "length": line.length,
    })

print(f"\n  Raw line angles: {[l['angle'] for l in noisy_lines]}")

# ── Step 2: Run consolidation ────────────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 2: Line Consolidation")
print("=" * 70)

w, h = result.image_dimensions
print(f"  Image: {w} x {h} px")

consolidator = LineConsolidator(
    image_width=w,
    image_height=h,
    border_threshold=5.0,
    angle_tolerance_deg=1.0,
    perp_dist_px=5.0,
    endpoint_gap_px=10.0,
)

cleaned = consolidator.consolidate(noisy_lines)
stats = consolidator.get_stats()

print(f"\n  Border-filtered: {stats['border_filtered']}")
print(f"  Clusters formed: {stats['clusters_formed']}")
print(f"  Output lines:    {stats['output_lines']}")
print(f"  Consolidation:   {stats['input_lines']} → {stats['output_lines']} "
      f"(ratio: {stats['consolidation_ratio']}:1)")

# ── Step 3: Display results ──────────────────────────────────────────────────
print("\n" + "=" * 70)
print("STEP 3: Consolidated Lines")
print("=" * 70)

for i, line in enumerate(cleaned):
    print(f"\n  Line {i+1}:")
    print(f"    ID:     {line['id']}")
    print(f"    Start:  {line['start']}")
    print(f"    End:    {line['end']}")
    print(f"    Angle:  {line['angle']}°")
    print(f"    Length: {line['length']} px")

# ── Step 4: Write output JSON ────────────────────────────────────────────────
output = {
    "input_count": stats["input_lines"],
    "output_count": stats["output_lines"],
    "stats": stats,
    "consolidated_lines": cleaned,
}

with open("consolidation_result.json", "w") as f:
    json.dump(output, f, indent=2)

print(f"\n✅ Results written to consolidation_result.json")