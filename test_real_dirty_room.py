#!/usr/bin/env python3
"""
Integration Test: Real dirty_room.pdf & dirty_room.dwg

Pipeline per source:
  1. Convert PDF/DWG → high-res PNG
  2. Raster extraction (OpenCV pipeline)
  3. Line consolidation
  4. Extract text + bounding boxes from PDF (as Textract proxy)
  5. Spatial Dimension Linking
  6. Output enriched JSON

Usage:
  python test_real_dirty_room.py
"""

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, "lambda/raster_line_extraction")

import cv2
import fitz
import numpy as np

from raster_line_extractor import extract_lines
from line_consolidator import LineConsolidator
from spatial_dimension_linker import SpatialDimensionLinker, TextractBlock

# ══════════════════════════════════════════════════════════════════════════════
# Helper: Convert PDF to high-res PNG and extract text positions
# ══════════════════════════════════════════════════════════════════════════════

def pdf_to_png_and_texts(pdf_path: str, dpi: int = 300):
    """Convert a PDF page to PNG and extract text blocks with positions.
    
    Returns:
        (png_path, image_width, image_height, text_blocks)
        where text_blocks is a list of TextractBlock objects with positions
        scaled to the PNG pixel coordinates.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    
    # Convert to high-res PNG
    scale = dpi / 72.0  # PDF points are 72 per inch
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat)
    
    tmp_png = tempfile.mktemp(suffix=".png", prefix="dirty_room_pdf_")
    pix.save(tmp_png)
    img_w, img_h = pix.width, pix.height
    
    print(f"  PDF → PNG: {img_w}×{img_h} px ({dpi} DPI, scale={scale:.2f})")
    
    # Extract text blocks with positions (scale to PNG coords)
    blocks_data = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    text_blocks = []
    
    for block in blocks_data:
        if block["type"] != 0:  # skip image blocks
            continue
        for line in block.get("lines", []):
            line_dir = line.get("dir", (1, 0))
            # direction vector: (1,0)=horizontal, (0,1)=vertical
            if abs(line_dir[0]) > abs(line_dir[1]):
                text_angle = 0.0  # horizontal
            else:
                text_angle = 90.0 if line_dir[1] > 0 else -90.0
            
            for span in line.get("spans", []):
                text = span["text"].strip()
                if not text:
                    continue
                
                bbox = span["bbox"]
                # Scale from PDF coords to pixel coords
                px_left = bbox[0] * scale
                px_top = bbox[1] * scale
                px_right = bbox[2] * scale
                px_bottom = bbox[3] * scale
                
                cx = (px_left + px_right) / 2.0
                cy = (px_top + px_bottom) / 2.0
                w = px_right - px_left
                h = px_bottom - px_top
                
                # Check if text is vertical (rotated 90°)
                # In PDF, if the line direction is vertical, the bbox may be swapped
                if h > w * 2 and text_angle != 0:
                    text_angle = 90.0
                
                text_blocks.append(TextractBlock(
                    block_id=f"pdf-{len(text_blocks)}",
                    text=text,
                    confidence=95.0,
                    cx=round(cx, 2),
                    cy=round(cy, 2),
                    angle=text_angle,
                    width=round(w, 2),
                    height=round(h, 2),
                ))
    
    doc.close()
    return tmp_png, img_w, img_h, text_blocks


def dwg_to_png(dwg_path: str):
    """Convert DWG to high-res PNG via Aspose.CAD.
    
    Returns:
        png_path or None if conversion fails.
    """
    try:
        import aspose.cad as cad
        from aspose.cad.imageoptions import CadRasterizationOptions, PngOptions
        
        img = cad.Image.load(dwg_path)
        
        # Use the original page size if available, else default
        raster = CadRasterizationOptions()
        raster.page_width = 3500.0
        raster.page_height = 2500.0
        raster.draw_type = cad.fileformats.cad.CadDrawTypeMode.USE_OBJECT_COLOR
        
        png_opts = PngOptions()
        png_opts.vector_rasterization_options = raster
        
        tmp_png = tempfile.mktemp(suffix=".png", prefix="dirty_room_dwg_")
        img.save(tmp_png, png_opts)
        
        # Get actual dimensions
        import cv2
        im = cv2.imread(tmp_png)
        if im is not None:
            h, w = im.shape[:2]
            print(f"  DWG → PNG: {w}×{h} px")
            return tmp_png
        
        return None
    except Exception as e:
        print(f"  DWG conversion failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline: Run full extraction + consolidation + linking
# ══════════════════════════════════════════════════════════════════════════════

def run_pipeline(png_path: str, text_blocks: list, source_name: str, img_w: int, img_h: int):
    """Run the full pipeline on a converted PNG image."""
    
    print(f"\n{'─' * 60}")
    print(f"  RASTER EXTRACTION: {source_name}")
    print(f"{'─' * 60}")
    
    # Stage 1: Raster extraction
    os.environ["HOUGH_THRESHOLD"] = "50"
    os.environ["HOUGH_MIN_LINE_LENGTH"] = "40"
    os.environ["HOUGH_MAX_LINE_GAP"] = "20"
    os.environ["MIN_LINE_LENGTH"] = "30"
    os.environ["LOG_LEVEL"] = "WARNING"
    
    import importlib
    import raster_line_extractor
    importlib.reload(raster_line_extractor)
    from raster_line_extractor import extract_lines
    
    result = extract_lines(png_path)
    w, h = result.image_dimensions
    print(f"  Image: {w}×{h}")
    print(f"  Raw lines: {len(result.lines)}")
    
    raw_lines = []
    for line in result.lines:
        raw_lines.append({
            "id": line.id,
            "start": line.start,
            "end": line.end,
            "angle": line.angle_degrees,
            "length": line.length,
        })
    
    # Stage 2: Consolidation
    print(f"\n  LINE CONSOLIDATION")
    consolidator = LineConsolidator(
        image_width=w,
        image_height=h,
        border_threshold=5.0,
        angle_tolerance_deg=1.5,
        perp_dist_px=8.0,
        endpoint_gap_px=15.0,
    )
    
    consolidated = consolidator.consolidate(raw_lines)
    stats = consolidator.get_stats()
    
    print(f"  Input: {stats['input_lines']} → Border-filtered: {stats['border_filtered']}")
    print(f"  Clusters: {stats['clusters_formed']} → Output: {stats['output_lines']}")
    
    # Categorize output lines
    h_lines = [l for l in consolidated if abs(l["angle"]) < 5]
    v_lines = [l for l in consolidated if abs(abs(l["angle"]) - 90) < 5]
    d_lines = [l for l in consolidated if abs(l["angle"]) > 5 and abs(abs(l["angle"]) - 90) > 5]
    print(f"  Horizontal: {len(h_lines)} | Vertical: {len(v_lines)} | Diagonal: {len(d_lines)}")
    
    # Stage 3: Filter significant wall lines (length > 50px)
    significant = [
        line for line in consolidated
        if math.hypot(
            line["end"][0] - line["start"][0],
            line["end"][1] - line["start"][1],
        ) > 50
    ]
    print(f"  Significant lines (>50px): {len(significant)}")
    
    # Stage 4: Spatial Dimension Linking
    print(f"\n  SPATIAL DIMENSION LINKING")
    
    # Print what text we're working with
    print(f"  Text blocks extracted from PDF: {len(text_blocks)}")
    dim_texts = [t for t in text_blocks if any(c in t.text for c in ["'", '"', '′', '″'])]
    label_texts = [t for t in text_blocks if t not in dim_texts]
    print(f"    Dimension-like texts: {len(dim_texts)}")
    print(f"    Label texts: {len(label_texts)}")
    
    for t in dim_texts:
        print(f"      '{t.text}' @ ({t.cx:.0f},{t.cy:.0f}) angle={t.angle}")
    
    linker = SpatialDimensionLinker(
        max_distance=200.0,         # generous — dimensions can be far from walls
        max_angle_diff=45.0,        # accept horizontal text near vertical walls
        midpoint_tolerance=0.8,     # dimension text near wall midpoint
        confidence_threshold=10.0,  # low threshold for PDF-extracted text
        filter_dimensions=True,     # only link dimension-like text
    )
    
    enriched = linker.link(significant, text_blocks)
    link_stats = linker.get_stats()
    
    print(f"\n  Linker Results:")
    print(f"    Input lines:   {link_stats['input_lines']}")
    print(f"    Text blocks:   {link_stats['text_blocks']}")
    print(f"    Matches:       {link_stats['matches']}")
    print(f"    Unmatched:     {link_stats['unmatched_lines']} lines, "
          f"{link_stats['unmatched_texts']} texts")
    
    # Format clean output
    clean = SpatialDimensionLinker.format_clean(enriched)
    
    walls_with_dims = [l for l in clean if "explicit_dimension" in l]
    walls_without_dims = [l for l in clean if "explicit_dimension" not in l]
    
    print(f"\n  Walls WITH dimensions: {len(walls_with_dims)}")
    for line in walls_with_dims:
        length = math.hypot(
            line["end"][0] - line["start"][0],
            line["end"][1] - line["start"][1],
        )
        print(f"    ✅ '{line['explicit_dimension']}' → "
              f"({line['start'][0]:.0f},{line['start'][1]:.0f})→"
              f"({line['end'][0]:.0f},{line['end'][1]:.0f}) len={length:.0f}")
    
    print(f"\n  Walls WITHOUT dimensions: {len(walls_without_dims)}")
    for line in walls_without_dims[:10]:
        length = math.hypot(
            line["end"][0] - line["start"][0],
            line["end"][1] - line["start"][1],
        )
        print(f"    ○ ({line['start'][0]:.0f},{line['start'][1]:.0f})→"
              f"({line['end'][0]:.0f},{line['end'][1]:.0f}) len={length:.0f}")
    if len(walls_without_dims) > 10:
        print(f"    ... and {len(walls_without_dims) - 10} more")
    
    return {
        "source": source_name,
        "image_size": [w, h],
        "pipeline": {
            "raster_lines_detected": len(result.lines),
            "consolidated_lines": stats["output_lines"],
            "significant_lines": len(significant),
            "dimensions_linked": len(walls_with_dims),
        },
        "walls": clean,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

all_results = {}

# ── Test 1: dirty_room.pdf ─────────────────────────────────────────────────
print("=" * 70)
print("TEST 1: dirty_room.pdf (Real Architectural Plan)")
print("=" * 70)

if os.path.exists("dirty_room.pdf"):
    png_path, img_w, img_h, text_blocks = pdf_to_png_and_texts("dirty_room.pdf", dpi=300)
    result = run_pipeline(png_path, text_blocks, "dirty_room.pdf", img_w, img_h)
    all_results["pdf"] = result
    
    # Save enriched output
    with open("dirty_room_pdf_result.json", "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n  📄 Saved: dirty_room_pdf_result.json")
    
    os.unlink(png_path)
    print(f"  🧹 Cleaned up temp PNG")
else:
    print("  ⚠️ dirty_room.pdf not found — skipping")

# ── Test 2: dirty_room.dwg ─────────────────────────────────────────────────
print("\n" + "=" * 70)
print("TEST 2: dirty_room.dwg (CAD Original)")
print("=" * 70)

if os.path.exists("dirty_room.dwg"):
    dwg_png = dwg_to_png("dirty_room.dwg")
    if dwg_png:
        # For DWG, we don't have text extraction — use the PDF's texts
        # scaled to DWG PNG coordinates as a reasonable proxy
        print("  Note: Using PDF-extracted text (coordinate-adjusted) as Textract proxy for DWG")
        
        # Get PDF text blocks if available
        if os.path.exists("dirty_room.pdf"):
            _, pdf_w, pdf_h, pdf_texts = pdf_to_png_and_texts("dirty_room.pdf", dpi=300)
            
            # Read DWG PNG dimensions
            import cv2 as cv2_mod
            dwg_img = cv2_mod.imread(dwg_png)
            dwg_h, dwg_w = dwg_img.shape[:2]
            
            # Scale text coordinates from PDF PNG to DWG PNG
            sx = dwg_w / pdf_w
            sy = dwg_h / pdf_h
            dwg_texts = []
            for t in pdf_texts:
                dwg_texts.append(TextractBlock(
                    block_id=t.block_id + "_dwg",
                    text=t.text,
                    confidence=t.confidence,
                    cx=t.cx * sx,
                    cy=t.cy * sy,
                    angle=t.angle,
                    width=t.width * sx,
                    height=t.height * sy,
                ))
            
            result = run_pipeline(dwg_png, dwg_texts, "dirty_room.dwg", dwg_w, dwg_h)
            all_results["dwg"] = result
            
            with open("dirty_room_dwg_result.json", "w") as f:
                json.dump(result, f, indent=2)
            print(f"\n  📄 Saved: dirty_room_dwg_result.json")
        else:
            # Run without text — just extraction + consolidation
            print("  No PDF available for text proxy — running extraction only")
            os.environ["HOUGH_THRESHOLD"] = "50"
            os.environ["HOUGH_MIN_LINE_LENGTH"] = "40"
            os.environ["HOUGH_MAX_LINE_GAP"] = "20"
            os.environ["MIN_LINE_LENGTH"] = "30"
            os.environ["LOG_LEVEL"] = "WARNING"
            
            import importlib
            import raster_line_extractor
            importlib.reload(raster_line_extractor)
            from raster_line_extractor import extract_lines
            
            result_lines = extract_lines(dwg_png)
            print(f"  Raw lines: {len(result_lines.lines)}")
        
        os.unlink(dwg_png)
        print(f"  🧹 Cleaned up temp PNG")
    else:
        print("  ⚠️ DWG conversion failed — skipping")
else:
    print("  ⚠️ dirty_room.dwg not found — skipping")

# ── Summary ─────────────────────────────────────────────────────────────────
print("\n" + "#" * 70)
print("  REAL DIRTY ROOM TEST SUMMARY")
print("#" * 70)

for source, data in all_results.items():
    p = data["pipeline"]
    dim_count = sum(1 for w in data["walls"] if "explicit_dimension" in w)
    print(f"\n  {data['source']}:")
    print(f"    Image:           {data['image_size'][0]}×{data['image_size'][1]}")
    print(f"    Raw raster lines: {p['raster_lines_detected']}")
    print(f"    Consolidated:     {p['consolidated_lines']}")
    print(f"    Significant:      {p['significant_lines']}")
    print(f"    Dimensions linked: {p['dimensions_linked']}")
    
    # Show unique dimension values
    dims = [w["explicit_dimension"] for w in data["walls"] if "explicit_dimension" in w]
    if dims:
        print(f"    Dimension values: {set(dims)}")

print("\n" + "#" * 70)
print("  TEST COMPLETE ✅")
print("#" * 70)