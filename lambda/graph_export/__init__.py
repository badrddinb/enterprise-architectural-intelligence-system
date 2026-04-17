"""
Graph Export Microservice — CertifiedMathGraph → IFC & GeoJSON

Translates a resolved CertifiedMathGraph JSON into industry-standard 3D/Web formats:
  - IFC4 (ISO 16739) via IfcOpenShell — wall extrusions at 3.0 m
  - GeoJSON FeatureCollection — walls as LineString features
"""

__version__ = "1.0.0"