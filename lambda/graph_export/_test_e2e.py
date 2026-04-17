"""
End-to-End Test — Graph Export Microservice

Validates the full pipeline:
  1. Synthetic CertifiedMathGraph JSON creation (4-room floor plan).
  2. Graph loading and schema validation.
  3. IFC4 export via IfcOpenShell — verify walls, extrusion, re-readability.
  4. GeoJSON export — verify FeatureCollection structure and feature count.

Run:
  cd lambda/graph_export && python _test_e2e.py

Requirements:
  pip install ifcopenshell jsonschema
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure local imports work when running from the package directory
sys.path.insert(0, str(Path(__file__).resolve().parent))

from exceptions import InvalidGraphError, IFCExportError, GeoJSONExportError
from graph_loader import load_graph, GraphData, DEFAULT_WALL_THICKNESS
from ifc_exporter import export_ifc, DEFAULT_EXTRUSION_HEIGHT
from geojson_exporter import export_geojson


# ===========================================================================
# Synthetic CertifiedMathGraph — Two-room floor plan
# ===========================================================================
#
#   (0,4) n4──────────n3 (6,4)
#         │  Room 1    │
#         │  24 m²     │
#   (0,0) n1──────────n2──────────n5 (10,0)
#                      │  Room 2   │
#                      │  16 m²    │
#                      n6 (10,4)───┘
#
#  6 wall edges, 2 faces (rooms), 6 nodes
# ===========================================================================

def make_synthetic_graph() -> dict:
    """Build a complete CertifiedMathGraph JSON for a two-room floor plan."""

    # ── Nodes ─────────────────────────────────────────────────────────────
    n1_id = "00000000-0000-0000-0000-000000000001"
    n2_id = "00000000-0000-0000-0000-000000000002"
    n3_id = "00000000-0000-0000-0000-000000000003"
    n4_id = "00000000-0000-0000-0000-000000000004"
    n5_id = "00000000-0000-0000-0000-000000000005"
    n6_id = "00000000-0000-0000-0000-000000000006"

    nodes = [
        {"nodeId": n1_id, "pointId": str(uuid.uuid4()), "x": 0.0, "y": 0.0, "nodeType": "wall-corner", "degree": 2},
        {"nodeId": n2_id, "pointId": str(uuid.uuid4()), "x": 6.0, "y": 0.0, "nodeType": "wall-intersection", "degree": 3},
        {"nodeId": n3_id, "pointId": str(uuid.uuid4()), "x": 6.0, "y": 4.0, "nodeType": "wall-intersection", "degree": 3},
        {"nodeId": n4_id, "pointId": str(uuid.uuid4()), "x": 0.0, "y": 4.0, "nodeType": "wall-corner", "degree": 2},
        {"nodeId": n5_id, "pointId": str(uuid.uuid4()), "x": 10.0, "y": 0.0, "nodeType": "wall-corner", "degree": 2},
        {"nodeId": n6_id, "pointId": str(uuid.uuid4()), "x": 10.0, "y": 4.0, "nodeType": "wall-corner", "degree": 2},
    ]

    # ── Edges ─────────────────────────────────────────────────────────────
    e1_id = "aaaaaaaa-0000-0000-0000-000000000001"
    e2_id = "aaaaaaaa-0000-0000-0000-000000000002"
    e3_id = "aaaaaaaa-0000-0000-0000-000000000003"
    e4_id = "aaaaaaaa-0000-0000-0000-000000000004"
    e5_id = "aaaaaaaa-0000-0000-0000-000000000005"
    e6_id = "aaaaaaaa-0000-0000-0000-000000000006"
    e7_id = "aaaaaaaa-0000-0000-0000-000000000007"

    edges = [
        # Room 1 walls
        {"edgeId": e1_id, "fromNodeId": n1_id, "toNodeId": n2_id, "edgeType": "wall-segment",
         "weight": 6.0, "thickness": 0.25, "properties": {"material": "concrete", "isStructural": True}},
        {"edgeId": e2_id, "fromNodeId": n2_id, "toNodeId": n3_id, "edgeType": "wall-segment",
         "weight": 4.0, "thickness": 0.25, "properties": {"material": "concrete", "isStructural": True}},
        {"edgeId": e3_id, "fromNodeId": n3_id, "toNodeId": n4_id, "edgeType": "wall-segment",
         "weight": 6.0, "thickness": 0.25, "properties": {"material": "concrete", "isStructural": True}},
        {"edgeId": e4_id, "fromNodeId": n4_id, "toNodeId": n1_id, "edgeType": "wall-segment",
         "weight": 4.0, "thickness": 0.25, "properties": {"material": "masonry"}},
        # Room 2 walls
        {"edgeId": e5_id, "fromNodeId": n2_id, "toNodeId": n5_id, "edgeType": "wall-segment",
         "weight": 4.0, "thickness": 0.20},
        {"edgeId": e6_id, "fromNodeId": n5_id, "toNodeId": n6_id, "edgeType": "wall-segment",
         "weight": 4.0, "thickness": 0.20},
        {"edgeId": e7_id, "fromNodeId": n6_id, "toNodeId": n3_id, "edgeType": "wall-segment",
         "weight": 4.0, "thickness": 0.20},
    ]

    # ── Faces ─────────────────────────────────────────────────────────────
    f1_id = "bbbbbbbb-0000-0000-0000-000000000001"
    f2_id = "bbbbbbbb-0000-0000-0000-000000000002"

    faces = [
        {
            "faceId": f1_id,
            "boundaryEdgeIds": [e1_id, e2_id, e3_id, e4_id],
            "area": 24.0,
            "perimeter": 20.0,
            "faceType": "room",
            "properties": {"occupancyType": "residential", "isFireRated": False},
        },
        {
            "faceId": f2_id,
            "boundaryEdgeIds": [e5_id, e6_id, e7_id, e2_id],
            "area": 16.0,
            "perimeter": 16.0,
            "faceType": "room",
            "properties": {"occupancyType": "residential", "isFireRated": False},
        },
    ]

    # ── Certification ─────────────────────────────────────────────────────
    certification = {
        "isCertified": True,
        "certifiedAt": datetime.now(timezone.utc).isoformat(),
        "certifiedBy": {
            "userId": "00000000-0000-0000-0000-999999999999",
            "email": "test@arch-intel.systems",
            "role": "system-auditor",
        },
        "algorithmVersion": "1.0.0",
        "checks": [
            {
                "checkId": str(uuid.uuid4()),
                "checkName": "graph-connectivity",
                "passed": True,
                "description": "Graph is fully connected.",
            },
        ],
    }

    return {
        "graphId": str(uuid.uuid4()),
        "sourceCoordinatesId": str(uuid.uuid4()),
        "sourceFileId": str(uuid.uuid4()),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "topologyType": "planar",
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "faceCount": len(faces),
        "nodes": nodes,
        "edges": edges,
        "faces": faces,
        "certification": certification,
    }


# ===========================================================================
# Test Functions
# ===========================================================================


def test_graph_loader():
    """Test 1: Load and validate a synthetic CertifiedMathGraph."""
    print("\n" + "=" * 70)
    print("TEST 1: Graph Loader")
    print("=" * 70)

    raw = make_synthetic_graph()
    graph = load_graph(raw)

    assert graph.node_count == 6, f"Expected 6 nodes, got {graph.node_count}"
    assert graph.edge_count == 7, f"Expected 7 edges, got {graph.edge_count}"
    assert graph.wall_count == 7, f"Expected 7 wall edges, got {graph.wall_count}"
    assert graph.face_count == 2, f"Expected 2 faces, got {graph.face_count}"

    # Test node resolution
    edge = list(graph.edges_by_id.values())[0]
    (x1, y1), (x2, y2) = graph.resolve_edge_coords(edge)
    assert x1 == 0.0 and y1 == 0.0, f"Expected (0,0), got ({x1},{y1})"
    assert x2 == 6.0 and y2 == 0.0, f"Expected (6,0), got ({x2},{y2})"

    # Test face polygon resolution
    face = list(graph.faces_by_id.values())[0]
    ring = graph.resolve_face_polygon(face)
    assert len(ring) >= 5, f"Expected closed ring with ≥5 points, got {len(ring)}"

    print(f"  ✓ Nodes: {graph.node_count}")
    print(f"  ✓ Edges: {graph.edge_count} ({graph.wall_count} walls)")
    print(f"  ✓ Faces: {graph.face_count}")
    print(f"  ✓ Edge coord resolution: ({x1},{y1}) → ({x2},{y2})")
    print(f"  ✓ Face polygon ring: {len(ring)} points")
    print("  PASSED ✓")
    return graph


def test_graph_loader_invalid():
    """Test 2: Verify that an invalid graph raises InvalidGraphError."""
    print("\n" + "=" * 70)
    print("TEST 2: Graph Loader — Invalid Input")
    print("=" * 70)

    # Missing required field
    bad_graph = {"nodes": [], "edges": [], "faces": []}
    try:
        load_graph(bad_graph)
        assert False, "Should have raised InvalidGraphError"
    except InvalidGraphError as exc:
        print(f"  ✓ Correctly raised InvalidGraphError: {exc.message[:80]}...")

    # Referential integrity: edge references non-existent node
    raw = make_synthetic_graph()
    raw["edges"].append({
        "edgeId": str(uuid.uuid4()),
        "fromNodeId": "ffffffff-0000-0000-0000-000000000000",  # doesn't exist
        "toNodeId": "00000000-0000-0000-0000-000000000001",
        "edgeType": "wall-segment",
    })
    try:
        load_graph(raw)
        assert False, "Should have raised InvalidGraphError for bad node ref"
    except InvalidGraphError as exc:
        print(f"  ✓ Caught referential integrity error: {exc.message[:80]}...")

    print("  PASSED ✓")


def test_ifc_export(graph: GraphData):
    """Test 3: Export to IFC4 and validate the output."""
    print("\n" + "=" * 70)
    print("TEST 3: IFC4 Export (IfcOpenShell)")
    print("=" * 70)

    with tempfile.TemporaryDirectory(prefix="graph_export_test_") as tmpdir:
        ifc_path = Path(tmpdir) / "test_output.ifc"

        result_path = export_ifc(
            graph,
            ifc_path,
            extrusion_height=3.0,
            project_name="Test Project",
            building_name="Test Building",
        )

        assert result_path.exists(), f"IFC file not created at {result_path}"
        file_size = result_path.stat().st_size
        assert file_size > 0, "IFC file is empty"

        print(f"  ✓ IFC file created: {result_path}")
        print(f"  ✓ File size: {file_size:,} bytes")

        # ── Re-read the IFC file with IfcOpenShell ───────────────────────
        import ifcopenshell
        model = ifcopenshell.open(str(result_path))

        # Verify schema
        schema_name = model.schema
        assert schema_name == "IFC4", f"Expected IFC4 schema, got {schema_name}"
        print(f"  ✓ Schema: {schema_name}")

        # Count walls
        walls = model.by_type("IfcWallStandardCase")
        assert len(walls) == 7, f"Expected 7 IfcWallStandardCase, got {len(walls)}"
        print(f"  ✓ IfcWallStandardCase count: {len(walls)}")

        # Verify spatial hierarchy
        projects = model.by_type("IfcProject")
        assert len(projects) >= 1, "No IfcProject found"
        print(f"  ✓ IfcProject: {projects[0].Name}")

        buildings = model.by_type("IfcBuilding")
        assert len(buildings) >= 1, "No IfcBuilding found"
        print(f"  ✓ IfcBuilding: {buildings[0].Name}")

        storeys = model.by_type("IfcBuildingStorey")
        assert len(storeys) >= 1, "No IfcBuildingStorey found"
        print(f"  ✓ IfcBuildingStorey: {storeys[0].Name}")

        # Verify a wall has geometry
        sample_wall = walls[0]
        assert sample_wall.Representation is not None, "Wall has no Representation"
        print(f"  ✓ Sample wall '{sample_wall.Name}' has representation")

        # Verify containment
        containment_rels = model.by_type("IfcRelContainedInSpatialStructure")
        contained_walls = []
        for rel in containment_rels:
            contained_walls.extend(rel.RelatedElements)
        assert len(contained_walls) >= 7, (
            f"Expected ≥7 walls in storey containment, got {len(contained_walls)}"
        )
        print(f"  ✓ Walls in spatial structure: {len(contained_walls)}")

        # Verify materials
        materials = model.by_type("IfcMaterial")
        material_names = {m.Name for m in materials}
        print(f"  ✓ Materials: {material_names}")
        assert "concrete" in material_names, "Expected 'concrete' material"

    print("  PASSED ✓")


def test_geojson_export(graph: GraphData):
    """Test 4: Export to GeoJSON FeatureCollection and validate."""
    print("\n" + "=" * 70)
    print("TEST 4: GeoJSON Export")
    print("=" * 70)

    with tempfile.TemporaryDirectory(prefix="graph_export_test_") as tmpdir:
        geojson_path = Path(tmpdir) / "test_output.geojson"

        result = export_geojson(graph, geojson_path)

        # Validate top-level structure
        assert result["type"] == "FeatureCollection", (
            f"Expected 'FeatureCollection', got '{result['type']}'"
        )
        print(f"  ✓ type: FeatureCollection")

        features = result["features"]
        assert len(features) >= 7, f"Expected ≥7 features, got {len(features)}"
        print(f"  ✓ Total features: {len(features)}")

        # Count feature types
        linestrings = [f for f in features if f["geometry"]["type"] == "LineString"]
        polygons = [f for f in features if f["geometry"]["type"] == "Polygon"]
        print(f"  ✓ LineString features (edges): {len(linestrings)}")
        print(f"  ✓ Polygon features (faces): {len(polygons)}")

        assert len(linestrings) == 7, f"Expected 7 LineStrings, got {len(linestrings)}"
        assert len(polygons) == 2, f"Expected 2 Polygons, got {len(polygons)}"

        # Validate first LineString
        ls = linestrings[0]
        coords = ls["geometry"]["coordinates"]
        assert len(coords) == 2, f"LineString should have 2 coordinates, got {len(coords)}"
        assert len(coords[0]) == 2, "Each coordinate should be [x, y]"
        print(f"  ✓ LineString coords: {coords}")

        # Validate properties on first wall feature
        props = ls["properties"]
        assert "edgeId" in props, "Missing edgeId in properties"
        assert props["edgeType"] == "wall-segment", f"Expected wall-segment, got {props['edgeType']}"
        assert "length" in props, "Missing length in properties"
        assert "thickness" in props, "Missing thickness in properties"
        print(f"  ✓ Edge properties: edgeType={props['edgeType']}, length={props['length']}, thickness={props['thickness']}")

        # Validate first Polygon
        if polygons:
            poly = polygons[0]
            ring = poly["geometry"]["coordinates"][0]
            assert len(ring) >= 4, f"Polygon ring should have ≥4 coords, got {len(ring)}"
            # Check ring is closed
            assert ring[0] == ring[-1], "Polygon ring is not closed"
            print(f"  ✓ Polygon ring: {len(ring)} points (closed={ring[0] == ring[-1]})")
            poly_props = poly["properties"]
            assert "faceType" in poly_props, "Missing faceType in polygon properties"
            assert "area" in poly_props, "Missing area in polygon properties"
            print(f"  ✓ Face properties: faceType={poly_props['faceType']}, area={poly_props['area']}")

        # Validate metadata
        meta = result["properties"]
        assert meta["sourceGraphId"] == graph.graph_id
        print(f"  ✓ Metadata: graphId={meta['sourceGraphId'][:8]}...")

        # Validate file was written
        assert geojson_path.exists(), "GeoJSON file not written"
        with open(geojson_path, encoding="utf-8") as f:
            written = json.load(f)
        assert written["type"] == "FeatureCollection"
        print(f"  ✓ File written: {geojson_path.name} ({geojson_path.stat().st_size:,} bytes)")

    print("  PASSED ✓")


def test_ifc_custom_height(graph: GraphData):
    """Test 5: Verify custom extrusion height propagates to IFC walls."""
    print("\n" + "=" * 70)
    print("TEST 5: IFC Custom Extrusion Height (4.5 m)")
    print("=" * 70)

    import ifcopenshell

    custom_height = 4.5

    with tempfile.TemporaryDirectory(prefix="graph_export_test_") as tmpdir:
        ifc_path = Path(tmpdir) / "test_custom_height.ifc"
        export_ifc(graph, ifc_path, extrusion_height=custom_height)

        model = ifcopenshell.open(str(ifc_path))
        walls = model.by_type("IfcWallStandardCase")
        assert len(walls) == 7

        # Check extrusion depth on first wall
        rep = walls[0].Representation
        body_reps = [r for r in rep.Representations if r.RepresentationIdentifier == "Body"]
        assert body_reps, "No Body representation found"

        solid = body_reps[0].Items[0]
        assert solid.is_a("IfcExtrudedAreaSolid")
        depth = solid.Depth
        assert abs(depth - custom_height) < 1e-6, (
            f"Expected extrusion depth {custom_height}, got {depth}"
        )
        print(f"  ✓ Extrusion depth: {depth} m (expected {custom_height} m)")

    print("  PASSED ✓")


def test_geojson_no_file(graph: GraphData):
    """Test 6: GeoJSON export to dict only (no file written)."""
    print("\n" + "=" * 70)
    print("TEST 6: GeoJSON In-Memory Only (no file path)")
    print("=" * 70)

    result = export_geojson(graph, output_path=None)
    assert result["type"] == "FeatureCollection"
    assert len(result["features"]) > 0
    print(f"  ✓ Features in memory: {len(result['features'])}")
    print("  PASSED ✓")


# ===========================================================================
# Main Runner
# ===========================================================================


def main():
    """Run all E2E tests."""
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║          Graph Export — End-to-End Test Suite                       ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")

    tests_passed = 0
    tests_failed = 0

    # Test 1: Graph loader
    try:
        graph = test_graph_loader()
        tests_passed += 1
    except Exception as exc:
        print(f"  FAILED ✗: {exc}")
        tests_failed += 1
        return

    # Test 2: Invalid graph handling
    try:
        test_graph_loader_invalid()
        tests_passed += 1
    except Exception as exc:
        print(f"  FAILED ✗: {exc}")
        tests_failed += 1

    # Test 3: IFC export
    try:
        test_ifc_export(graph)
        tests_passed += 1
    except Exception as exc:
        print(f"  FAILED ✗: {exc}")
        import traceback
        traceback.print_exc()
        tests_failed += 1

    # Test 4: GeoJSON export
    try:
        test_geojson_export(graph)
        tests_passed += 1
    except Exception as exc:
        print(f"  FAILED ✗: {exc}")
        tests_failed += 1

    # Test 5: Custom extrusion height
    try:
        test_ifc_custom_height(graph)
        tests_passed += 1
    except Exception as exc:
        print(f"  FAILED ✗: {exc}")
        import traceback
        traceback.print_exc()
        tests_failed += 1

    # Test 6: GeoJSON in-memory only
    try:
        test_geojson_no_file(graph)
        tests_passed += 1
    except Exception as exc:
        print(f"  FAILED ✗: {exc}")
        tests_failed += 1

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"Results: {tests_passed} passed, {tests_failed} failed")
    print("=" * 70)

    if tests_failed > 0:
        sys.exit(1)
    else:
        print("All tests passed! ✓")


if __name__ == "__main__":
    main()