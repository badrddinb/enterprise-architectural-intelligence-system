"""
Graph Loader — Load, validate, and index a CertifiedMathGraph JSON.

Provides a `GraphData` dataclass that pre-indexes nodes, edges, and faces
for O(1) lookups during export.  Validates the input against the
certified-math-graph.schema.json contract.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import jsonschema

from exceptions import InvalidGraphError

logger = logging.getLogger("graph_export")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "schemas" / "certified-math-graph.schema.json"

# Edge types that represent physical wall segments
WALL_EDGE_TYPES: frozenset[str] = frozenset({"wall-segment"})

# Default wall thickness (metres) when edge.properties.thickness is absent
DEFAULT_WALL_THICKNESS = 0.20

# Default extrusion height (metres) per the task specification
DEFAULT_EXTRUSION_HEIGHT = 3.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class NodeRecord:
    """Lightweight wrapper around a single graph node."""

    __slots__ = ("node_id", "x", "y", "z", "node_type", "degree", "properties")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.node_id: str = raw["nodeId"]
        self.x: float = float(raw["x"])
        self.y: float = float(raw["y"])
        self.z: float = float(raw.get("z", 0.0))
        self.node_type: str = raw["nodeType"]
        self.degree: int = raw.get("degree", 0)
        self.properties: dict[str, Any] = raw.get("properties", {})


class EdgeRecord:
    """Lightweight wrapper around a single graph edge."""

    __slots__ = ("edge_id", "from_node_id", "to_node_id", "edge_type",
                 "weight", "is_directed", "thickness", "properties")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.edge_id: str = raw["edgeId"]
        self.from_node_id: str = raw["fromNodeId"]
        self.to_node_id: str = raw["toNodeId"]
        self.edge_type: str = raw["edgeType"]
        self.weight: float | None = raw.get("weight")
        self.is_directed: bool = raw.get("isDirected", False)
        self.thickness: float = raw.get("thickness", DEFAULT_WALL_THICKNESS)
        self.properties: dict[str, Any] = raw.get("properties", {})

    @property
    def is_wall(self) -> bool:
        return self.edge_type in WALL_EDGE_TYPES


class FaceRecord:
    """Lightweight wrapper around a single graph face."""

    __slots__ = ("face_id", "boundary_edge_ids", "area", "perimeter",
                 "face_type", "properties")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.face_id: str = raw["faceId"]
        self.boundary_edge_ids: list[str] = raw["boundaryEdgeIds"]
        self.area: float = float(raw["area"])
        self.perimeter: float | None = raw.get("perimeter")
        self.face_type: str | None = raw.get("faceType")
        self.properties: dict[str, Any] = raw.get("properties", {})


class GraphData:
    """Pre-indexed, validated representation of a CertifiedMathGraph.

    Attributes:
        graph_id:           UUID of the source graph.
        source_file_id:     UUID of the original uploaded file.
        nodes_by_id:        ``dict[str, NodeRecord]`` — O(1) node lookup.
        edges_by_id:        ``dict[str, EdgeRecord]`` — O(1) edge lookup.
        faces_by_id:        ``dict[str, FaceRecord]`` — O(1) face lookup.
        wall_edges:         ``list[EdgeRecord]`` — only wall-segment edges.
        raw:                The original JSON dict (for metadata access).
    """

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.graph_id: str = raw["graphId"]
        self.source_file_id: str = raw.get("sourceFileId", "")

        # Index nodes
        self.nodes_by_id: dict[str, NodeRecord] = {}
        for n in raw.get("nodes", []):
            rec = NodeRecord(n)
            self.nodes_by_id[rec.node_id] = rec

        # Index edges
        self.edges_by_id: dict[str, EdgeRecord] = {}
        self.wall_edges: list[EdgeRecord] = []
        for e in raw.get("edges", []):
            rec = EdgeRecord(e)
            self.edges_by_id[rec.edge_id] = rec
            if rec.is_wall:
                self.wall_edges.append(rec)

        # Index faces
        self.faces_by_id: dict[str, FaceRecord] = {}
        for f in raw.get("faces", []):
            rec = FaceRecord(f)
            self.faces_by_id[rec.face_id] = rec

    # -- Convenience helpers ------------------------------------------------

    @property
    def node_count(self) -> int:
        return len(self.nodes_by_id)

    @property
    def edge_count(self) -> int:
        return len(self.edges_by_id)

    @property
    def face_count(self) -> int:
        return len(self.faces_by_id)

    @property
    def wall_count(self) -> int:
        return len(self.wall_edges)

    def get_node(self, node_id: str) -> NodeRecord:
        """Return a node or raise ``InvalidGraphError``."""
        try:
            return self.nodes_by_id[node_id]
        except KeyError:
            raise InvalidGraphError(
                f"Edge references unknown node '{node_id}'",
                graph_id=self.graph_id,
                violations=[f"missing-node:{node_id}"],
            )

    def resolve_edge_coords(self, edge: EdgeRecord) -> tuple[tuple[float, float], tuple[float, float]]:
        """Resolve an edge to its ((x1,y1), (x2,y2)) start/end coordinates."""
        start = self.get_node(edge.from_node_id)
        end = self.get_node(edge.to_node_id)
        return (start.x, start.y), (end.x, end.y)

    def resolve_face_polygon(self, face: FaceRecord) -> list[list[float]]:
        """Resolve a face's boundary edges into an ordered ring of [x, y] coords.

        The algorithm walks the boundary edges sequentially, matching the end
        of one edge to the start of the next, producing a closed ring suitable
        for GeoJSON Polygon geometry.
        """
        if not face.boundary_edge_ids:
            return []

        coords: list[list[float]] = []
        for eid in face.boundary_edge_ids:
            edge = self.edges_by_id.get(eid)
            if edge is None:
                logger.warning(
                    "Face %s references unknown edge %s — skipping face polygon",
                    face.face_id, eid,
                )
                return []
            start, end = self.resolve_edge_coords(edge)

            if not coords:
                coords.append(list(start))
            # Check connectivity: last coord should match start of this edge
            last = coords[-1]
            if (abs(last[0] - start[0]) > 1e-6 or abs(last[1] - start[1]) > 1e-6):
                # Try reversed direction
                if (abs(last[0] - end[0]) < 1e-6 and abs(last[1] - end[1]) < 1e-6):
                    coords.append(list(start))
                else:
                    # Tolerant: just append both (best effort ordering)
                    coords.append(list(start))
            coords.append(list(end))

        # Close the ring
        if coords and (abs(coords[-1][0] - coords[0][0]) > 1e-6
                       or abs(coords[-1][1] - coords[0][1]) > 1e-6):
            coords.append(coords[0])

        return coords


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

_schema_cache: dict[str, Any] | None = None


def _load_schema() -> dict[str, Any]:
    """Load and cache the CertifiedMathGraph JSON schema."""
    global _schema_cache
    if _schema_cache is None:
        schema_file = Path(os.environ.get(
            "GRAPH_SCHEMA_PATH", str(_SCHEMA_PATH)
        ))
        if not schema_file.exists():
            logger.warning(
                "Schema file not found at %s — skipping schema validation",
                schema_file,
            )
            return {}
        with open(schema_file, encoding="utf-8") as f:
            _schema_cache = json.load(f)
    return _schema_cache


def _validate_schema(data: dict[str, Any]) -> list[str]:
    """Validate *data* against the CertifiedMathGraph schema.

    Returns a list of human-readable violation strings (empty if valid).
    """
    schema = _load_schema()
    if not schema:
        return []

    validator_cls = jsonschema.Draft202012Validator
    validator = validator_cls(schema)
    violations: list[str] = []
    for error in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in error.absolute_path) or "(root)"
        violations.append(f"{path}: {error.message}")
    return violations


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_graph(source: str | dict[str, Any] | Path) -> GraphData:
    """Load and validate a CertifiedMathGraph from a file path or dict.

    Args:
        source: A file path (``str`` or ``Path``) to a JSON file, or a
                pre-parsed ``dict``.

    Returns:
        A fully indexed ``GraphData`` instance.

    Raises:
        InvalidGraphError: If the data fails schema or integrity checks.
    """
    # ── Parse input ──────────────────────────────────────────────────────
    if isinstance(source, dict):
        data = source
    else:
        path = Path(source)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            raise InvalidGraphError(
                f"Cannot read graph JSON from '{source}': {exc}",
                violations=[f"read-error:{source}"],
            ) from exc

    graph_id = data.get("graphId", "unknown")

    # ── Schema validation ────────────────────────────────────────────────
    violations = _validate_schema(data)
    if violations:
        raise InvalidGraphError(
            f"Graph '{graph_id}' failed schema validation with {len(violations)} violation(s)",
            graph_id=graph_id,
            violations=violations,
        )

    # ── Integrity checks ─────────────────────────────────────────────────
    integrity_issues: list[str] = []

    node_ids = {n["nodeId"] for n in data.get("nodes", [])}
    for e in data.get("edges", []):
        if e["fromNodeId"] not in node_ids:
            integrity_issues.append(
                f"edge:{e['edgeId']}:fromNodeId:{e['fromNodeId']}:not-found"
            )
        if e["toNodeId"] not in node_ids:
            integrity_issues.append(
                f"edge:{e['edgeId']}:toNodeId:{e['toNodeId']}:not-found"
            )

    edge_ids = {e["edgeId"] for e in data.get("edges", [])}
    for f in data.get("faces", []):
        for eid in f["boundaryEdgeIds"]:
            if eid not in edge_ids:
                integrity_issues.append(
                    f"face:{f['faceId']}:edge:{eid}:not-found"
                )

    if integrity_issues:
        raise InvalidGraphError(
            f"Graph '{graph_id}' has {len(integrity_issues)} referential integrity issue(s)",
            graph_id=graph_id,
            violations=integrity_issues,
        )

    # ── Build indexed graph ──────────────────────────────────────────────
    graph = GraphData(data)

    logger.info(
        "Graph loaded: %d nodes, %d edges (%d walls), %d faces",
        graph.node_count, graph.edge_count, graph.wall_count, graph.face_count,
    )

    return graph