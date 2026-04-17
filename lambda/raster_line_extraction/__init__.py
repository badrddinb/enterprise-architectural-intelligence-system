"""
Raster Line Extraction Lambda — Converts noisy pixel plans into pure mathematical vectors.

Part of the Enterprise Architectural Intelligence System.
Uses OpenCV to apply computer vision pipelines (grayscale, blur, Canny edge detection,
Hough Line Transform) with deterministic affine correction to extract vector lines
from rasterized architectural plans (scanned PDFs, PNGs, TIFFs).

Triggered by the Raster-Processing-Queue (SQS).
Output conforms to the raw-coordinates.schema.json data contract.
"""

__version__ = "1.0.0"