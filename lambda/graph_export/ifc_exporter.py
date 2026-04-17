"""
IFC4 Exporter — CertifiedMathGraph → IfcWallStandardCase (ISO 16739).

Converts every 2D wall edge (``edgeType == "wall-segment"``) into an
``IfcWallStandardCase`` object, extruding the Z-axis by 3.0 m (configurable).

Geometry strategy per wall:
  1. Resolve the edge's start/end 2D coordinates from the node index.
  2. Compute midpoint, length, and in-plane rotation angle.
  3. Define an ``IfcRectangleProfileDef`` (length × thickness) centred at
     the midpoint, oriented along the wall direction.
  4. ``IfcExtrudedAreaSolid`` along +Z by ``extrusion_height`` metres.
  5. Assign ``IfcLocalPlacement`` at the midpoint with correct rotation.

Dependencies:
  - ifcopenshell >= 0.7
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.guid

from exceptions import IFCExportError
from graph_loader import DEFAULT_WALL_THICKNESS, GraphData

logger = logging.getLogger("graph_export")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_EXTRUSION_HEIGHT = 3.0  # metres
DEFAULT_WALL_THICKNESS_IFC = 0.20  # metres
DEFAULT_PRECISION = 1e-5


# ---------------------------------------------------------------------------
# GUID Helper
# ---------------------------------------------------------------------------

_guid_counter = 0


def _new_guid() -> str:
    """Generate a new IFC-compatible GUID."""
    return ifcopenshell.guid.new()


# ---------------------------------------------------------------------------
# IFC Project Skeleton Builder
# ---------------------------------------------------------------------------


def _create_project_skeleton(
    model: ifcopenshell.file,
    *,
    project_name: str = "Exported Project",
    building_name: str = "Exported Building",
    storey_name: str = "Ground Floor",
    extrusion_height: float = DEFAULT_EXTRUSION_HEIGHT,
) -> dict[str, Any]:
    """Create the IFC spatial hierarchy: Project → Site → Building → Storey.

    Also sets up SI units (metres) and the geometric representation context.

    Returns a dict with all created entities for downstream reference.
    """
    # ── Units ────────────────────────────────────────────────────────────
    unit_assignment = model.create_entity("IfcUnitAssignment")
    length_unit = model.create_entity(
        "IfcSIUnit",
        UnitType="LENGTHUNIT",
        Name="METRE",
    )
    plane_angle_unit = model.create_entity(
        "IfcSIUnit",
        UnitType="PLANEANGLEUNIT",
        Name="RADIAN",
    )
    unit_assignment.Units = [length_unit, plane_angle_unit]

    # ── Project ──────────────────────────────────────────────────────────
    project = model.create_entity(
        "IfcProject",
        GlobalId=_new_guid(),
        Name=project_name,
    )
    project.UnitsInContext = unit_assignment

    # ── Representation Context (3D Model) ────────────────────────────────
    world_cs = model.create_entity("IfcAxis2Placement3D")
    model_context = model.create_entity(
        "IfcGeometricRepresentationContext",
        ContextIdentifier="Body",
        ContextType="Model",
        CoordinateSpaceDimension=3,
        Precision=DEFAULT_PRECISION,
        WorldCoordinateSystem=world_cs,
    )

    # ── Site ─────────────────────────────────────────────────────────────
    site_placement = model.create_entity(
        "IfcLocalPlacement",
        RelativePlacement=model.create_entity("IfcAxis2Placement3D"),
    )
    site = model.create_entity(
        "IfcSite",
        GlobalId=_new_guid(),
        Name="Site",
        ObjectPlacement=site_placement,
    )
    model.create_entity(
        "IfcRelAggregates",
        GlobalId=_new_guid(),
        RelatingObject=project,
        RelatedObjects=[site],
    )

    # ── Building ─────────────────────────────────────────────────────────
    building_placement = model.create_entity(
        "IfcLocalPlacement",
        PlacementRelTo=site_placement,
        RelativePlacement=model.create_entity("IfcAxis2Placement3D"),
    )
    building = model.create_entity(
        "IfcBuilding",
        GlobalId=_new_guid(),
        Name=building_name,
        ObjectPlacement=building_placement,
    )
    model.create_entity(
        "IfcRelAggregates",
        GlobalId=_new_guid(),
        RelatingObject=site,
        RelatedObjects=[building],
    )

    # ── Building Storey ──────────────────────────────────────────────────
    storey_placement = model.create_entity(
        "IfcLocalPlacement",
        PlacementRelTo=building_placement,
        RelativePlacement=model.create_entity("IfcAxis2Placement3D"),
    )
    storey = model.create_entity(
        "IfcBuildingStorey",
        GlobalId=_new_guid(),
        Name=storey_name,
        ObjectPlacement=storey_placement,
    )
    model.create_entity(
        "IfcRelAggregates",
        GlobalId=_new_guid(),
        RelatingObject=building,
        RelatedObjects=[storey],
    )

    return {
        "project": project,
        "model_context": model_context,
        "site": site,
        "building": building,
        "storey": storey,
    }


# ---------------------------------------------------------------------------
# Wall Geometry Builder
# ---------------------------------------------------------------------------


def _create_wall_geometry(
    model: ifcopenshell.file,
    model_context: Any,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    thickness: float,
    extrusion_height: float,
) -> tuple[Any, Any]:
    """Create the extruded solid geometry for a single wall.

    The wall is modelled as a rectangular profile (length × thickness)
    centred at the midpoint of the two endpoints, rotated to align with
    the edge direction, and extruded along +Z.

    Args:
        model:         IfcOpenShell file object.
        model_context: IfcGeometricRepresentationContext for body reps.
        x1, y1:        Start point of the wall axis.
        x2, y2:        End point of the wall axis.
        thickness:     Wall thickness in metres.
        extrusion_height: Z extrusion depth in metres.

    Returns:
        Tuple of (IfcLocalPlacement, IfcProductDefinitionShape).
    """
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)

    if length < 1e-9:
        logger.warning("Skipping degenerate wall (length ≈ 0)")
        return None, None

    # Midpoint
    mx = (x1 + x2) / 2.0
    my = (y1 + y2) / 2.0

    # Rotation angle about Z
    angle = math.atan2(dy, dx)

    # ── Profile: rectangle centred at origin ─────────────────────────────
    profile_placement = model.create_entity(
        "IfcAxis2Placement2D",
        Location=model.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0)),
    )
    profile = model.create_entity(
        "IfcRectangleProfileDef",
        ProfileType="AREA",
        Position=profile_placement,
        XDim=length,
        YDim=thickness,
    )

    # ── Extrusion position: midpoint + rotation ──────────────────────────
    extrusion_position = model.create_entity(
        "IfcAxis2Placement3D",
        Location=model.create_entity("IfcCartesianPoint", Coordinates=(mx, my, 0.0)),
        Axis=model.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0)),
        RefDirection=model.create_entity(
            "IfcDirection", DirectionRatios=(math.cos(angle), math.sin(angle), 0.0)
        ),
    )

    # ── Extruded solid ───────────────────────────────────────────────────
    solid = model.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=profile,
        Position=extrusion_position,
        ExtrudedDirection=model.create_entity(
            "IfcDirection", DirectionRatios=(0.0, 0.0, 1.0)
        ),
        Depth=extrusion_height,
    )

    # ── Body shape representation ────────────────────────────────────────
    body_rep = model.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=model_context,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[solid],
    )

    # ── Axis shape representation (2D centreline) ────────────────────────
    axis_points = model.create_entity(
        "IfcPolyline",
        Points=[
            model.create_entity("IfcCartesianPoint", Coordinates=(x1, y1, 0.0)),
            model.create_entity("IfcCartesianPoint", Coordinates=(x2, y2, 0.0)),
        ],
    )
    # Sub-context inherits CoordinateSpaceDimension from parent
    axis_context = model.create_entity(
        "IfcGeometricRepresentationSubContext",
        ParentContext=model_context,
        ContextIdentifier="Axis",
        ContextType="Model",
    )
    axis_rep = model.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=axis_context,
        RepresentationIdentifier="Axis",
        RepresentationType="Curve2D",
        Items=[axis_points],
    )

    # ── Product definition shape ─────────────────────────────────────────
    product_shape = model.create_entity(
        "IfcProductDefinitionShape",
        Name="Wall",
        Representations=[axis_rep, body_rep],
    )

    # ── Local placement at origin (wall position encoded in solid) ───────
    placement = model.create_entity(
        "IfcLocalPlacement",
        RelativePlacement=model.create_entity("IfcAxis2Placement3D"),
    )

    return placement, product_shape


# ---------------------------------------------------------------------------
# Public API — Function A
# ---------------------------------------------------------------------------


def export_ifc(
    graph: GraphData,
    output_path: str | Path,
    *,
    extrusion_height: float = DEFAULT_EXTRUSION_HEIGHT,
    project_name: str | None = None,
    building_name: str | None = None,
    storey_name: str = "Ground Floor",
) -> Path:
    """Export a CertifiedMathGraph as an IFC4 file (ISO 16739).

    Every 2D wall line (``edgeType == "wall-segment"``) is converted into
    an ``IfcWallStandardCase`` with a rectangular cross-section extruded
    along the Z-axis by *extrusion_height* metres (default 3.0 m).

    Args:
        graph:             A validated, indexed ``GraphData`` instance.
        output_path:       Destination file path for the ``.ifc`` output.
        extrusion_height:  Z-axis extrusion depth in metres (default 3.0).
        project_name:      Optional IFC project name.
        building_name:     Optional IFC building name.
        storey_name:       IFC building storey name.

    Returns:
        ``Path`` to the written ``.ifc`` file.

    Raises:
        IFCExportError: If IFC generation fails.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if project_name is None:
        project_name = f"Graph-{graph.graph_id[:8]}"
    if building_name is None:
        building_name = f"Building-{graph.source_file_id[:8]}" if graph.source_file_id else "Building"

    try:
        model = ifcopenshell.file(schema="IFC4")

        # ── Build spatial hierarchy ──────────────────────────────────────
        skeleton = _create_project_skeleton(
            model,
            project_name=project_name,
            building_name=building_name,
            storey_name=storey_name,
            extrusion_height=extrusion_height,
        )
        storey = skeleton["storey"]
        model_context = skeleton["model_context"]

        # ── Create walls ─────────────────────────────────────────────────
        wall_count = 0
        skipped = 0

        for edge in graph.wall_edges:
            try:
                (x1, y1), (x2, y2) = graph.resolve_edge_coords(edge)
            except Exception as exc:
                logger.warning(
                    "Skipping wall edge %s: cannot resolve coords — %s",
                    edge.edge_id, exc,
                )
                skipped += 1
                continue

            thickness = edge.thickness if edge.thickness and edge.thickness > 0 else DEFAULT_WALL_THICKNESS_IFC

            placement, product_shape = _create_wall_geometry(
                model, model_context,
                x1, y1, x2, y2,
                thickness, extrusion_height,
            )

            if placement is None:
                skipped += 1
                continue

            # ── IfcWallStandardCase entity ──────────────────────────────
            wall_name = f"Wall-{edge.edge_id[:8]}"
            wall = model.create_entity(
                "IfcWallStandardCase",
                GlobalId=_new_guid(),
                Name=wall_name,
                Description=f"edgeType={edge.edge_type}; thickness={thickness}m",
                ObjectPlacement=placement,
                Representation=product_shape,
            )

            # Assign wall to storey
            model.create_entity(
                "IfcRelContainedInSpatialStructure",
                GlobalId=_new_guid(),
                RelatingStructure=storey,
                RelatedElements=[wall],
            )

            # ── Optional material assignment ────────────────────────────
            material_str = edge.properties.get("material") if edge.properties else None
            if material_str:
                ifc_material = model.create_entity(
                    "IfcMaterial",
                    Name=material_str,
                )
                model.create_entity(
                    "IfcRelAssociatesMaterial",
                    GlobalId=_new_guid(),
                    RelatedObjects=[wall],
                    RelatingMaterial=ifc_material,
                )

            wall_count += 1

        # ── Write IFC file ───────────────────────────────────────────────
        model.write(str(output_path))

        logger.info(
            "IFC export complete: %d walls created, %d skipped → %s",
            wall_count, skipped, output_path,
        )

        if wall_count == 0:
            logger.warning(
                "No walls were exported — graph may contain no wall-segment edges"
            )

        return output_path

    except IFCExportError:
        raise
    except Exception as exc:
        raise IFCExportError(
            f"IFC export failed: {exc}",
            graph_id=graph.graph_id,
            wall_count=0,
        ) from exc