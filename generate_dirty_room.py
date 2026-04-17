
#!/usr/bin/env python3
"""
Generate dirty_room.pdf — A simple architectural plan with:
  - 1 rectangular room (4 walls)
  - 1 door opening (gap in bottom wall)
  - 2 dimension lines with text annotations (10'-0" and 15'-0")

Output: dirty_room.pdf uploaded to LocalStack S3.
"""

import math
import os
import sys

sys.path.insert(0, "lambda/raster_line_extraction")

import cv2
import numpy as np
import fitz  # PyMuPDF

# ── Room Geometry (in pixels, on a 1400×1000 canvas) ────────────────────────

CANVAS_W = 1400
CANVAS_H = 1000
BG_COLOR = 255       # white paper
WALL_COLOR = 0        # black ink
WALL_THICKNESS = 3
DIM_COLOR = 80        # dark gray for dimension lines
DIM_THICKNESS = 1
TEXT_COLOR = 0

# Room bounding box
ROOM_LEFT = 200
ROOM_TOP = 200
ROOM_RIGHT = 1200     # 1000px wide → represents ~15 feet
ROOM_BOTTOM = 800     # 600px tall → represents ~10 feet

# Door opening (gap in bottom wall)
DOOR_START_X = 600
DOOR_END_X = 800
DOOR_Y = ROOM_BOTTOM

# Dimension line offsets (distance from wall)
DIM_OFFSET = 60

# ── Step 1: Draw the architectural plan as a high-res image ─────────────────

image = np.full((CANVAS_H, CANVAS_W), BG_COLOR, dtype=np.uint8)

# Draw 4 walls of the room
# Top wall (full)
cv2.line(image, (ROOM_LEFT, ROOM_TOP), (ROOM_RIGHT, ROOM_TOP),
         WALL_COLOR, WALL_THICKNESS)
# Left wall (full)
cv2.line(image, (ROOM_LEFT, ROOM_TOP), (ROOM_LEFT, ROOM_BOTTOM),
         WALL_COLOR, WALL_THICKNESS)
# Right wall (full)
cv2.line(image, (ROOM_RIGHT, ROOM_TOP), (ROOM_RIGHT, ROOM_BOTTOM),
         WALL_COLOR, WALL_THICKNESS)

# Bottom wall — with door gap
cv2.line(image, (ROOM_LEFT, ROOM_BOTTOM), (DOOR_START_X, ROOM_BOTTOM),
         WALL_COLOR, WALL_THICKNESS)
cv2.line(image, (DOOR_END_X, ROOM_BOTTOM), (ROOM_RIGHT, ROOM_BOTTOM),
         WALL_COLOR, WALL_THICKNESS)

# Door arc (quarter-circle to indicate swing)
arc_center = (DOOR_END_X, DOOR_Y)
arc_radius = DOOR_END_X - DOOR_START_X
cv2.ellipse(
    image,
    arc_center,
    (arc_radius, arc_radius),
    0, 180, 270,  # quarter arc
    DIM_COLOR, 1,
)

# ── Horizontal dimension line (width = 15'-0") ──────────────────────────────
# Positioned below the room
dim_y = ROOM_BOTTOM + DIM_OFFSET
# Extension lines (from wall down to dimension line)
cv2.line(image, (ROOM_LEFT, ROOM_BOTTOM + 10), (ROOM_LEFT, dim_y + 5),
         DIM_COLOR, DIM_THICKNESS)
cv2.line(image, (ROOM_RIGHT, ROOM_BOTTOM + 10), (ROOM_RIGHT, dim_y + 5),
         DIM_COLOR, DIM_THICKNESS)
# Dimension line itself
cv2.line(image, (ROOM_LEFT, dim_y), (ROOM_RIGHT, dim_y),
         DIM_COLOR, DIM_THICKNESS)
# Tick marks at endpoints
for tx in (ROOM_LEFT, ROOM_RIGHT):
    cv2.line(image, (tx, dim_y - 8), (tx, dim_y + 8), DIM_COLOR, DIM_THICKNESS)

# Dimension text "15'-0\"" centered on the dimension line
dim_text_x = (ROOM_LEFT + ROOM_RIGHT) // 2
dim_text_y = dim_y + 20
# Use a white rectangle behind text for readability
cv2.rectangle(image,
              (dim_text_x - 45, dim_text_y - 14),
              (dim_text_x + 45, dim_text_y + 8),
              BG_COLOR, -1)
cv2.putText(image, "15'-0\"", (dim_text_x - 35, dim_text_y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, TEXT_COLOR, 1, cv2.LINE_AA)

# ── Vertical dimension line (height = 10'-0") ──────────────────────────────
# Positioned to the right of the room
dim_x = ROOM_RIGHT + DIM_OFFSET
# Extension lines (from wall right to dimension line)
cv2.line(image, (ROOM_RIGHT + 10, ROOM_TOP), (dim_x + 5, ROOM_TOP),
         DIM_COLOR, DIM_THICKNESS)
cv2.line(image, (ROOM_RIGHT + 10, ROOM_BOTTOM), (dim_x + 5, ROOM_BOTTOM),
         DIM_COLOR, DIM_THICKNESS)
# Dimension line itself
cv2.line(image, (dim_x, ROOM_TOP), (dim_x, ROOM_BOTTOM),
         DIM_COLOR, DIM_THICKNESS)
# Tick marks at endpoints
for ty in (ROOM_TOP, ROOM_BOTTOM):
    cv2.line(image, (dim_x - 8, ty), (dim_x + 8, ty), DIM_COLOR, DIM_THICKNESS)

# Dimension text "10'-0\"" centered vertically on the dimension line
# Written horizontally next to the vertical dimension line
vert_text_x = dim_x + 12
vert_text_y = (ROOM_TOP + ROOM_BOTTOM) // 2
cv2.rectangle(image,
              (vert_text_x - 2, vert_text_y - 14),
              (vert_text_x + 60, vert_text_y + 8),
              BG_COLOR, -1)
cv2.putText(image, "10'-0\"", (vert_text_x, vert_text_y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, TEXT_COLOR, 1, cv2.LINE_AA)

# ── Step 2: Save as PDF via PyMuPDF ─────────────────────────────────────────

# Save image temporarily
temp_png = "_dirty_room_temp.png"
cv2.imwrite(temp_png, image)

# Convert to PDF
pdf_doc = fitz.open()
img_doc = fitz.open(temp_png)  # open the PNG as a document
pdf_bytes = img_doc.convert_to_pdf()
pdf_doc = fitz.open("pdf", pdf_bytes)
pdf_page = pdf_doc[0]
pdf_page.set_rect(fitz.Rect(0, 0, CANVAS_W, CANVAS_H))

# Save the PDF
pdf_path = "dirty_room.pdf"
pdf_doc.save(pdf_path)
pdf_doc.close()
img_doc.close()

# Clean up temp image
os.unlink(temp_png)

print(f"✅ Created: {pdf_path} ({CANVAS_W}×{CANVAS_H} px)")
print(f"   Room: {ROOM_RIGHT - ROOM_LEFT}×{ROOM_BOTTOM - ROOM_TOP} px")
print(f"   Door gap: {DOOR_END_X - DOOR_START_X} px (bottom wall)")
print(f"   Dimension texts: '15'-0\"' (horizontal), '10'-0\"' (vertical)")

# ── Step 3: Upload to LocalStack S3 if available ────────────────────────────

try:
    import boto3
    from botocore.config import Config

    endpoint = os.environ.get("AWS_ENDPOINT_URL_EXTERNAL", "http://localhost:4566")
    s3 = boto3.client(
        "s3",
        region_name="us-east-1",
        endpoint_url=endpoint,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(retries={"max_attempts": 2, "mode": "adaptive"}),
    )

    bucket = "arch-ingestion-bucket"
    key = f"uploads/dirty_room.pdf"

    # Create bucket if needed
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        s3.create_bucket(Bucket=bucket)

    s3.upload_file(pdf_path, bucket, key)
    print(f"\n✅ Uploaded: s3://{bucket}/{key} → {endpoint}")

except Exception as e:
    print(f"\n⚠️  S3 upload skipped (LocalStack not running?): {e}")
    print(f"   Upload manually: awslocal s3 cp {pdf_path} s3://arch-ingestion-bucket/")