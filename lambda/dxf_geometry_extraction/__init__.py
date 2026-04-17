"""
DXF Geometry Extraction Lambda — Extracts geometric truth from CAD files.

Part of the Enterprise Architectural Intelligence System.
Parses DXF files using ezdxf to extract LINE, LWPOLYLINE, and MTEXT entities
into a standardized JSON array of mathematical edges and points.

Triggered by the Vector-Processing-Queue (SQS).
Output conforms to the raw-coordinates.schema.json data contract.
"""

__version__ = "1.0.0"