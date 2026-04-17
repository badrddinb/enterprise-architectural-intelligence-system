"""
GeoJSON Exporter — CertifiedMathGraph → FeatureCollection (RFC 7946).

Converts the graph's edges into LineString features and faces into Polygon
features within a standard GeoJSON FeatureCollection.

Every edge becomes a Feature:
  - geometry:  LineString [[x1, y1], [x2, y2]]
  - properties: edgeId, edgeType, weight (length), thickness, material, isDirected

Every face becomes a Feature:
  - geometry:  Polygon (closed ring resolved from boundary edge nodes)
  - properties: faceId, faceType, area, perimeter

Dependencies:
  - Standard library only (dataclasses, json, math).
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from exceptions import GeoJSONExportError
from graph_loader import GraphData

logger = logging.getLogger("graph_export")

# ---------------------------------------------------------------------------
# GeoJSON specification constants
# ---------------------------------------------------------------------------

GEOJSON_SPEC_URI = "https://geojson.org/schema/FeatureCollection.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_feature(
    feature_id: str,
    geometry_type: str,
    coordinates: list[Any],
    properties: dict[str, Any],
) -> dict[str, Any]:
    """Build a single GeoJSON Feature dict."""
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {
            "type": geometry_type,
            "coordinates": coordinates,
        },
        "properties": properties,
    }


def _clean_properties(props: dict[str, Any]) -> dict[str, Any]:
    """Remove None values from a properties dict for cleaner JSON output."""
    return {k: v for k, v in props.items() if v is not None}


# ---------------------------------------------------------------------------
# Edge → LineString Feature
# ---------------------------------------------------------------------------


def _edge_to_feature(graph: GraphData, edge_idx: int) -> dict[str, Any] | None:
    """Convert a single graph edge to a GeoJSON Feature (LineString).

    Returns None if the edge's nodes cannot be resolved.
    """
    # Access edges by iterating (GraphData stores as dict)
    edges = list(graph.edges_by_id.values())
    if edge_idx >= len(edges):
        return None
    edge = edges[edge_idx]

    try:
        (x1, y1), (x2, y2) = graph.resolve_edge_coords(edge)
    except Exception as exc:
        logger.warning(
            "Skipping GeoJSON feature for edge %s: %s",
            edge.edge_id, exc,
        )
        return None

    # Compute length if weight is missing
    weight = edge.weight
    if weight is None:
        weight = round(math.hypot(x2 - x1, y2 - y1), 6)

    properties = _clean_properties({
        "edgeId": edge.edge_id,
        "edgeType": edge.edge_type,
        "fromNodeId": edge.from_node_id,
        "toNodeId": edge.to_node_id,
        "length": round(weight, 6),
        "thickness": edge.thickness,
        "isDirected": edge.is_directed,
        "material": edge.properties.get("material") if edge.properties else None,
        "isStructural": edge.properties.get("isStructural") if edge.properties else None,
        "isLoadBearing": edge.properties.get("isLoadBearing") if edge.properties else None,
        "fireRating": edge.properties.get("fireRating") if edge.properties else None,
    })

    return _make_feature(
        feature_id=edge.edge_id,
        geometry_type="LineString",
        coordinates=[[x1, y1], [x2, y2]],
        properties=properties,
    )


# ---------------------------------------------------------------------------
# Face → Polygon Feature
# ---------------------------------------------------------------------------


def _face_to_feature(graph: GraphData, face_idx: int) -> dict[str, Any] | None:
    """Convert a single graph face to a GeoJSON Feature (Polygon).

    Returns None if the face's boundary cannot be resolved.
    """
    faces = list(graph.faces_by_id.values())
    if face_idx >= len(faces):
        return None
    face = faces[face_idx]

    ring = graph.resolve_face_polygon(face)
    if len(ring) < 4:
        # A valid polygon ring needs at least 4 positions (triangle + closing point)
        logger.warning(
            "Face %s has insufficient boundary coordinates (%d) — skipping polygon",
            face.face_id, len(ring),
        )
        return None

    properties = _clean_properties({
        "faceId": face.face_id,
        "faceType": face.face_type,
        "area": round(face.area, 6),
        "perimeter": round(face.perimeter, 6) if face.perimeter is not None else None,
        "boundaryEdgeCount": len(face.boundary_edge_ids),
        "occupancyType": face.properties.get("occupancyType") if face.properties else None,
        "isFireRated": face.properties.get("isFireRated") if face.properties else None,
        "isMeansOfEgress": face.properties.get("isMeansOfEgress") if face.properties else None,
    })

    return _make_feature(
        feature_id=face.face_id,
        geometry_type="Polygon",
        coordinates=[ring],  # Polygon = array of linear rings
        properties=properties,
    )


# ---------------------------------------------------------------------------
# Public API — Function B
# ---------------------------------------------------------------------------


def export_geojson(
    graph: GraphData,
    output_path: str | Path | None = None,
    *,
    include_edges: bool = True,
    include_faces: bool = True,
) -> dict[str, Any]:
    """Export a CertifiedMathGraph as a GeoJSON FeatureCollection.

    Walls (edges) are emitted as ``LineString`` features.  Faces are emitted
    as ``Polygon`` features.

    Args:
        graph:         A validated, indexed ``GraphData`` instance.
        output_path:   Optional destination file path for the ``.geojson``
                       output.  If ``None``, only the dict is returned.
        include_edges: Whether to include edge LineString features (default True).
        include_faces: Whether to include face Polygon features (default True).

    Returns:
        A GeoJSON ``FeatureCollection`` as a plain Python dict.

    Raises:
        GeoJSONExportError: If GeoJSON generation fails.
    """
    try:
        features: list[dict[str, Any]] = []

        # ── Edge features (LineString) ────────────────────────────────────
        edge_feature_count = 0
        if include_edges:
            for idx in range(len(graph.edges_by_id)):
                feature = _edge_to_feature(graph, idx)
                if feature is not None:
                    features.append(feature)
                    edge_feature_count += 1

        # ── Face features (Polygon) ───────────────────────────────────────
        face_feature_count = 0
        if include_faces:
            for idx in range(len(graph.faces_by_id)):
                feature = _face_to_feature(graph, idx)
                if feature is not None:
                    features.append(feature)
                    face_feature_count += 1

        # ── Assemble FeatureCollection ────────────────────────────────────
        feature_collection: dict[str, Any] = {
            "type": "FeatureCollection",
            "features": features,
            "properties": {
                "sourceGraphId": graph.graph_id,
                "sourceFileId": graph.source_file_id,
                "totalFeatures": len(features),
                "edgeFeatures": edge_feature_count,
                "faceFeatures": face_feature_count,
            },
        }

        # ── Write to file if path provided ────────────────────────────────
        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(feature_collection, f, indent=2, ensure_ascii=False)

            logger.info(
                "GeoJSON export complete: %d edge features, %d face features → %s",
                edge_feature_count, face_feature_count, output_path,
            )

        return feature_collection

    except GeoJSONExportError:
        raise
    except Exception as exc:
        raise GeoJSONExportError(
            f"GeoJSON export failed: {exc}",
            graph_id=graph.graph_id,
            feature_count=0,
        ) from exc