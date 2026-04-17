#!/usr/bin/env python3
"""
Integration Test: dirty_room.pdf & dirty_room.dwg via DEPLOYED Docker services.

Flow:
  1. Convert PDF → PNG, upload to LocalStack S3
  2. POST to raster-line-extraction service (port 8002) with SQS event
  3. Run line consolidation on service output
  4. Run spatial dimension linking with text from PDF
  5. Write enriched JSON output

Also tests DWG → PNG through the same pipeline.

Usage:
  python test_service_dirty_room.py
"""

import json
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, "lambda/raster_line_extraction")

import cv2
import fitz
import requests

from line_consolidator import LineConsolidator
from spatial_dimension_linker import SpatialDimensionLinker, TextractBlock

# ─── Configuration ────────────────────────────────────────────────────────────
S3_ENDPOINT = "http://localhost:4566"
S3_BUCKET = "arch-ingestion-bucket"
RASTER_SERVICE = "http://localhost:8002"
AWS_REGION = "us-east-1"

import boto3
s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id="test",
    aws_secret_access_key="test",
    region_name=AWS_REGION,
)


def ensure_bucket():
    """Ensure the S3 bucket exists in LocalStack."""
    try:
        s3.head_bucket(Bucket=S3_BUCKET)
        print(f"  ✅ Bucket '{S3_BUCKET}' exists")
    except Exception:
        s3.create_bucket(Bucket=S3_BUCKET)
        print(f"  📦 Created bucket '{S3_BUCKET}'")


def pdf_to_png(pdf_path: str, dpi: int = 300) -> str:
    """Convert PDF to high-res PNG."""
    doc = fitz.open(pdf_path)
    page = doc[0]
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat)
    
    tmp_png = tempfile.mktemp(suffix=".png", prefix="dirty_room_")
    pix.save(tmp_png)
    doc.close()
    
    im = cv2.imread(tmp_png)
    h, w = im.shape[:2]
    print(f"  Converted: {w}×{h} px ({dpi} DPI)")
    return tmp_png


def extract_text_blocks(pdf_path: str, dpi: int = 300) -> list:
    """Extract text blocks from PDF, scaled to PNG pixel coordinates."""
    doc = fitz.open(pdf_path)
    page = doc[0]
    scale = dpi / 72.0
    
    blocks_data = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    text_blocks = []
    
    for block in blocks_data:
        if block["type"] != 0:
            continue
        for line in block.get("lines", []):
            line_dir = line.get("dir", (1, 0))
            text_angle = 0.0 if abs(line_dir[0]) >= abs(line_dir[1]) else 90.0
            
            for span in line.get("spans", []):
                text = span["text"].strip()
                if not text:
                    continue
                
                bbox = span["bbox"]
                px_left = bbox[0] * scale
                px_top = bbox[1] * scale
                px_right = bbox[2] * scale
                px_bottom = bbox[3] * scale
                
                w = px_right - px_left
                h = px_bottom - px_top
                
                text_blocks.append(TextractBlock(
                    block_id=f"pdf-{len(text_blocks)}",
                    text=text,
                    confidence=95.0,
                    cx=round((px_left + px_right) / 2.0, 2),
                    cy=round((px_top + px_bottom) / 2.0, 2),
                    angle=text_angle,
                    width=round(w, 2),
                    height=round(h, 2),
                ))
    
    doc.close()
    return text_blocks


def upload_to_s3(local_path: str, s3_key: str) -> str:
    """Upload a file to LocalStack S3."""
    s3.upload_file(local_path, S3_BUCKET, s3_key)
    uri = f"s3://{S3_BUCKET}/{s3_key}"
    print(f"  Uploaded: {uri}")
    return uri


def call_raster_service(s3_uri: str, file_id: str, file_name: str) -> dict:
    """Call the deployed raster-line-extraction service via HTTP."""
    payload = {
        "Records": [{
            "body": json.dumps({
                "storageUri": s3_uri,
                "fileId": file_id,
                "fileName": file_name,
                "mimeType": "image/png",
            })
        }]
    }
    
    print(f"  Calling raster-line-extraction service at {RASTER_SERVICE}/invoke ...")
    resp = requests.post(f"{RASTER_SERVICE}/invoke", json=payload, timeout=120)
    
    if resp.status_code != 200:
        print(f"  ❌ Service returned HTTP {resp.status_code}: {resp.text[:500]}")
        return None
    
    result = resp.json()
    status = result.get("status", "UNKNOWN")
    extracted = result.get("extractedCount", 0)
    errors = result.get("errorCount", 0)
    print(f"  Service status: {status} | Extracted: {extracted} | Errors: {errors}")
    
    return result


def run_consolidation_and_linking(service_result: dict, text_blocks: list, source_name: str):
    """Run consolidation + spatial linking on service output."""
    
    if not service_result or service_result.get("extractedCount", 0) == 0:
        print(f"  ⚠️ No extraction results to process for {source_name}")
        return None
    
    # Extract raw lines from service response
    record = service_result["results"][0]
    if record.get("status") != "EXTRACTED":
        print(f"  ❌ Service error: {record.get('error', {})}")
        return None
    
    data = record["data"]
    # Service has two formats: 'lines' (point refs) and 'edges' (flat coords)
    # Use 'edges' which has {id, type, start:[x,y], end:[x,y]}
    lines_data = data.get("edges", [])
    if not lines_data:
        # Fallback: try lines with start/end
        lines_data = data.get("lines", [])
    img_dims = data.get("imageDimensions", {})
    img_w = img_dims.get("width", 0)
    img_h = img_dims.get("height", 0)
    
    print(f"\n  Service returned {len(lines_data)} raw lines from {img_w}×{img_h} image")
    
    # Convert service output to consolidator input format
    raw_lines = []
    for i, line in enumerate(lines_data):
        start = line.get("start", line.get("p1", [0, 0]))
        end = line.get("end", line.get("p2", [0, 0]))
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        angle = math.degrees(math.atan2(dy, dx)) if (dx != 0 or dy != 0) else 0
        length = math.hypot(dx, dy)
        raw_lines.append({
            "id": line.get("id", f"L{i}"),
            "start": start,
            "end": end,
            "angle": round(angle, 2),
            "length": round(length, 2),
        })
    
    # ── Consolidation ────────────────────────────────────────────────────
    print(f"\n  LINE CONSOLIDATION")
    consolidator = LineConsolidator(
        image_width=img_w,
        image_height=img_h,
        border_threshold=5.0,
        angle_tolerance_deg=1.5,
        perp_dist_px=8.0,
        endpoint_gap_px=15.0,
    )
    try:
        consolidated = consolidator.consolidate(raw_lines)
    except Exception as e:
        print(f"  ❌ Consolidation failed: {e}")
        import traceback
        traceback.print_exc()
        return None
    stats = consolidator.get_stats()
    
    print(f"  Input: {stats.get('input_lines', '?')} → Border-filtered: {stats.get('border_filtered', '?')}")
    print(f"  Clusters: {stats.get('clusters_formed', '?')} → Output: {stats.get('output_lines', '?')}")
    
    # Filter significant lines
    significant = [
        line for line in consolidated
        if math.hypot(
            line["end"][0] - line["start"][0],
            line["end"][1] - line["start"][1],
        ) > 50
    ]
    print(f"  Significant lines (>50px): {len(significant)}")
    
    # ── Spatial Dimension Linking ────────────────────────────────────────
    print(f"\n  SPATIAL DIMENSION LINKING")
    dim_texts = [t for t in text_blocks if any(c in t.text for c in ["'", '"'])]
    print(f"  Text blocks: {len(text_blocks)} total, {len(dim_texts)} dimension-like")
    
    linker = SpatialDimensionLinker(
        max_distance=200.0,
        max_angle_diff=45.0,
        midpoint_tolerance=0.8,
        confidence_threshold=10.0,
        filter_dimensions=True,
    )
    
    enriched = linker.link(significant, text_blocks)
    link_stats = linker.get_stats()
    clean = SpatialDimensionLinker.format_clean(enriched)
    
    walls_with_dims = [l for l in clean if "explicit_dimension" in l]
    
    print(f"  Matches: {link_stats['matches']}")
    print(f"  Walls with dimensions: {len(walls_with_dims)}")
    
    for line in walls_with_dims[:15]:
        length = math.hypot(
            line["end"][0] - line["start"][0],
            line["end"][1] - line["start"][1],
        )
        print(f"    ✅ '{line['explicit_dimension']}' → "
              f"({line['start'][0]:.0f},{line['start'][1]:.0f})→"
              f"({line['end'][0]:.0f},{line['end'][1]:.0f}) len={length:.0f}")
    
    return {
        "source": source_name,
        "image_size": [img_w, img_h],
        "pipeline": {
            "raster_service_lines": len(lines_data),
            "consolidated_lines": stats["output_lines"],
            "significant_lines": len(significant),
            "dimensions_linked": len(walls_with_dims),
        },
        "walls": clean,
    }


def check_service_health():
    """Check if the raster-line-extraction service is healthy."""
    try:
        resp = requests.get(f"{RASTER_SERVICE}/health", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print(f"  ✅ Raster service healthy: {data}")
            return True
        else:
            print(f"  ❌ Service returned {resp.status_code}")
            return False
    except Exception as e:
        print(f"  ❌ Cannot reach raster service: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

print("=" * 70)
print("  SERVICE INTEGRATION TEST: dirty_room.pdf & dirty_room.dwg")
print("=" * 70)

# ── Pre-flight checks ─────────────────────────────────────────────────────
print("\n  Pre-flight checks:")
ensure_bucket()

if not check_service_health():
    print("\n  ⛔ Raster-line-extraction service is not responding.")
    print("  Run: docker compose up -d raster-line-extraction")
    sys.exit(1)

all_results = {}

# ── Test 1: dirty_room.pdf via service ─────────────────────────────────────
print("\n" + "─" * 70)
print("  TEST 1: dirty_room.pdf → S3 → raster service → consolidation → linker")
print("─" * 70)

if os.path.exists("dirty_room.pdf"):
    # Step 1: Convert PDF to PNG
    print("\n  Step 1: Convert PDF → PNG")
    png_path = pdf_to_png("dirty_room.pdf", dpi=300)
    
    # Step 2: Extract text from PDF
    print("  Step 2: Extract text blocks from PDF")
    text_blocks = extract_text_blocks("dirty_room.pdf", dpi=300)
    dim_texts = [t for t in text_blocks if any(c in t.text for c in ["'", '"'])]
    print(f"    {len(text_blocks)} text blocks ({len(dim_texts)} dimension-like)")
    
    # Step 3: Upload to S3
    print("  Step 3: Upload PNG to LocalStack S3")
    s3_key = f"dirty-room/{int(time.time())}_dirty_room.png"
    s3_uri = upload_to_s3(png_path, s3_key)
    
    # Step 4: Call raster service
    print("  Step 4: Call deployed raster-line-extraction service")
    service_result = call_raster_service(s3_uri, "dirty-room-pdf-001", "dirty_room.png")
    
    # Step 5: Consolidation + Linking
    print("  Step 5: Run consolidation + spatial dimension linking")
    result = run_consolidation_and_linking(service_result, text_blocks, "dirty_room.pdf (via service)")
    
    if result:
        all_results["pdf"] = result
        with open("dirty_room_service_pdf_result.json", "w") as f:
            json.dump(result, f, indent=2)
        print(f"\n  📄 Saved: dirty_room_service_pdf_result.json")
    
    # Cleanup
    os.unlink(png_path)
    print("  🧹 Cleaned up temp PNG")
else:
    print("  ⚠️ dirty_room.pdf not found")

# ── Test 2: dirty_room.dwg via service ─────────────────────────────────────
print("\n" + "─" * 70)
print("  TEST 2: dirty_room.dwg → PNG → S3 → raster service → consolidation → linker")
print("─" * 70)

if os.path.exists("dirty_room.dwg"):
    try:
        import aspose.cad as cad
        from aspose.cad.imageoptions import CadRasterizationOptions, PngOptions
        
        # Step 1: Convert DWG to PNG
        print("\n  Step 1: Convert DWG → PNG")
        img = cad.Image.load("dirty_room.dwg")
        raster = CadRasterizationOptions()
        raster.page_width = 3500.0
        raster.page_height = 2500.0
        raster.draw_type = cad.fileformats.cad.CadDrawTypeMode.USE_OBJECT_COLOR
        
        png_opts = PngOptions()
        png_opts.vector_rasterization_options = raster
        dwg_png = tempfile.mktemp(suffix=".png", prefix="dirty_room_dwg_")
        img.save(dwg_png, png_opts)
        
        im = cv2.imread(dwg_png)
        dwg_h, dwg_w = im.shape[:2]
        print(f"  Converted: {dwg_w}×{dwg_h} px")
        
        # Step 2: Get text blocks from PDF (as proxy for Textract)
        print("  Step 2: Using PDF text as Textract proxy")
        if os.path.exists("dirty_room.pdf"):
            pdf_texts = extract_text_blocks("dirty_room.pdf", dpi=300)
            doc = fitz.open("dirty_room.pdf")
            page = doc[0]
            scale = 300 / 72.0
            pdf_w = int(page.rect.width * scale)
            pdf_h = int(page.rect.height * scale)
            doc.close()
            
            # Scale text coords from PDF PNG space to DWG PNG space
            sx, sy = dwg_w / pdf_w, dwg_h / pdf_h
            dwg_texts = [
                TextractBlock(
                    block_id=t.block_id + "_dwg",
                    text=t.text,
                    confidence=t.confidence,
                    cx=t.cx * sx,
                    cy=t.cy * sy,
                    angle=t.angle,
                    width=t.width * sx,
                    height=t.height * sy,
                )
                for t in pdf_texts
            ]
            print(f"    {len(dwg_texts)} text blocks (scaled)")
        else:
            print("    ⚠️ No PDF for text proxy")
            dwg_texts = []
        
        # Step 3: Upload to S3
        print("  Step 3: Upload PNG to LocalStack S3")
        s3_key = f"dirty-room/{int(time.time())}_dirty_room_dwg.png"
        s3_uri = upload_to_s3(dwg_png, s3_key)
        
        # Step 4: Call raster service
        print("  Step 4: Call deployed raster-line-extraction service")
        service_result = call_raster_service(s3_uri, "dirty-room-dwg-001", "dirty_room_dwg.png")
        
        # Step 5: Consolidation + Linking
        print("  Step 5: Run consolidation + spatial dimension linking")
        result = run_consolidation_and_linking(service_result, dwg_texts, "dirty_room.dwg (via service)")
        
        if result:
            all_results["dwg"] = result
            with open("dirty_room_service_dwg_result.json", "w") as f:
                json.dump(result, f, indent=2)
            print(f"\n  📄 Saved: dirty_room_service_dwg_result.json")
        
        os.unlink(dwg_png)
        print("  🧹 Cleaned up temp PNG")
    
    except Exception as e:
        print(f"  ❌ DWG test failed: {e}")
        import traceback
        traceback.print_exc()
else:
    print("  ⚠️ dirty_room.dwg not found")

# ── Summary ─────────────────────────────────────────────────────────────────
print("\n" + "#" * 70)
print("  SERVICE INTEGRATION TEST SUMMARY")
print("#" * 70)

for source, data in all_results.items():
    p = data["pipeline"]
    dims = [w["explicit_dimension"] for w in data["walls"] if "explicit_dimension" in w]
    print(f"\n  {data['source']}:")
    print(f"    Image:              {data['image_size'][0]}×{data['image_size'][1]}")
    print(f"    Raster service lines: {p['raster_service_lines']}")
    print(f"    Consolidated:        {p['consolidated_lines']}")
    print(f"    Significant:         {p['significant_lines']}")
    print(f"    Dimensions linked:   {p['dimensions_linked']}")
    if dims:
        # Only show actual dimension values (filter out labels)
        real_dims = [d for d in dims if any(c in d for c in ["'", "′"])]
        labels = [d for d in dims if d not in real_dims]
        print(f"    Dimensions: {set(real_dims)}")
        if labels:
            print(f"    Labels matched: {set(labels)}")

print("\n" + "#" * 70)
print("  SERVICE TEST COMPLETE ✅")
print("#" * 70)