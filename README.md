# Enterprise Architectural Intelligence System

> A serverless pipeline that transforms architectural plan files (PDF, DXF, DWG, raster) into **certified mathematical graphs** and evaluates them for **building-code compliance** — with human-in-the-loop approval gates at every stage.

---

## Table of Contents

- [System Overview](#system-overview)
- [Mathematical Foundation](#mathematical-foundation)
  - [1. Raster-to-Vector Extraction (Computer Vision)](#1-raster-to-vector-extraction-computer-vision)
  - [2. Line Consolidation (Post-Hough Fragment Merging)](#2-line-consolidation-post-hough-fragment-merging)
  - [3. Spatial Dimension Linking (OCR → Graph Mapping)](#3-spatial-dimension-linking-ocr--graph-mapping)
  - [4. DXF Geometry Extraction (Vector Parsing)](#4-dxf-geometry-extraction-vector-parsing)
  - [5. Certified Mathematical Graph Construction](#5-certified-mathematical-graph-construction)
  - [6. Graph Certification & Integrity Checks](#6-graph-certification--integrity-checks)
  - [7. Compliance Checking (Multi-Agent Pipeline)](#7-compliance-checking-multi-agent-pipeline)
- [Pipeline Architecture](#pipeline-architecture)
- [Data Contracts](#data-contracts)
- [Project Structure](#project-structure)

---

## System Overview

This system implements a **five-stage deterministic pipeline** governed by an AWS Step Functions state machine. Each stage performs mathematically rigorous operations on architectural data, with human approval gates between every transition:

```
Upload → Crop Zones → Extract Coordinates → Build Math Graph → Compliance Report
   │         │              │                     │                  │
   └── HITL──┘────── HITL ──┘──────── HITL ──────┘─────── HITL ────┘
```

The core innovation is the **CertifiedMathGraph** — a planar graph data structure where every node, edge, and face is mathematically verified and cryptographically checksummed, enabling deterministic compliance analysis against building codes.

---

## Mathematical Foundation

### 1. Raster-to-Vector Extraction (Computer Vision)

**Module:** `lambda/raster_line_extraction/raster_line_extractor.py`

Transforms raster images (scanned PDFs, PNGs, TIFFs) into mathematical line segments through an 8-stage computer vision pipeline:

#### 1.1 Gaussian Blur — Noise Suppression

A symmetric kernel G(σ) is convolved with the grayscale image I(x, y) to suppress scan noise and paper texture:

```
B(x, y) = (G(σ) * I)(x, y)
```

- Default kernel size: **5×5**
- Preserves architectural line boundaries while removing high-frequency noise

#### 1.2 Canny Edge Detection with Otsu Auto-Thresholding

Edge pixels are identified using the **Canny algorithm** with automatic threshold computation via **Otsu's method**:

1. Compute the image histogram
2. Find the optimal threshold T using Otsu's inter-class variance maximization
3. Set the high hysteresis threshold: `T_high = T_otsu`
4. Set the low hysteresis threshold: `T_low = 0.5 × T_otsu` (the standard 1:2 ratio recommended by Canny)
5. Apply non-maximum suppression and hysteresis edge tracking

This produces a binary edge map E(x, y) ∈ {0, 255}.

#### 1.3 Morphological Closing — Gap Bridging

A rectangular structuring element S is applied via morphological closing (dilation followed by erosion) to reconnect broken wall lines:

```
C = (E ⊕ S) ⊖ S
```

This bridges small gaps caused by text overlap, compression artifacts, or scan noise without significantly shifting edge positions.

#### 1.4 Probabilistic Hough Line Transform

The binary edge map is transformed into Hough parameter space (ρ, θ) where:

```
ρ = x·cos(θ) + y·sin(θ)
```

The **probabilistic** variant returns line segments [x₁, y₁, x₂, y₂] rather than infinite lines, with configurable parameters:
- `rho = 1` pixel (distance resolution)
- `theta = 1° ≈ π/180` radians (angle resolution)
- `threshold = 80` votes (minimum accumulator count)
- `minLineLength = 50` pixels
- `maxLineGap = 10` pixels

**Adaptive downscaling**: For images exceeding 8000px on either dimension, the edge map is temporarily downscaled for the Hough transform (which is O(N) in edge pixel count), then coordinates are mapped back to original resolution via inverse scaling.

#### 1.5 Deterministic Affine Correction (Orthogonal Constraint)

Each detected line undergoes **deterministic angle snapping** to correct scanner/photographer skew:

```
angle = atan2(dy, dx)  normalized to [-90°, 90°]

if angle ∈ [-1.5°, 1.5°]        → snap to exactly 0° (horizontal)
if |angle| ∈ [88.5°, 91.5°]     → snap to exactly ±90° (vertical)
otherwise                        → no correction
```

After snapping, the endpoint is recomputed by preserving the start point and Euclidean length:

```
x₂' = x₁ + L·cos(θ_target)
y₂' = y₁ + L·sin(θ_target)
```

This is a **rigid body rotation** — no scaling or shearing is introduced. Lines that are genuinely diagonal are left untouched.

#### 1.6 Point Deduplication

Coincident endpoints (where two wall lines meet at a corner) are merged via **coordinate rounding** into a single canonical point:

```
key = (round(x, precision), round(y, precision))
```

A hash-map registry ensures O(1) lookups and guarantees that shared vertices produce a single unique point ID.

---

### 2. Line Consolidation (Post-Hough Fragment Merging)

**Module:** `lambda/raster_line_extraction/line_consolidator.py`

A single architectural wall typically produces 10–30 fragmented Hough segments. This module implements a **five-stage post-processing pipeline**:

#### 2.1 Border Filtering

Lines with both endpoints within `BORDER_THRESHOLD` (5px) of the same image edge are removed as PDF page-frame artifacts.

#### 2.2 Collinear Clustering via Angular Grouping

Segments are sorted by normalized angle and grouped into clusters using two criteria:

1. **Angular distance**: `min(|a₁ - a₂|, 180° - |a₁ - a₂|) ≤ 1.0°`
2. **Perpendicular distance**: Both endpoints within `5px` of the cluster's reference line

The perpendicular distance from point (pₓ, pᵧ) to a line defined by reference point (rₓ, rᵧ) and unit normal (nₓ, nᵧ) is:

```
d = |(pₓ - rₓ)·nₓ + (pᵧ - rᵧ)·nᵧ|
```

#### 2.3 Spatial Merging via Scalar Projection

Within each cluster, all segment endpoints are **projected onto the shared direction axis**:

```
projection = (pₓ - rₓ)·dₓ + (pᵧ - rᵧ)·dᵧ
```

The projected intervals [lo, hi] are sorted and merged greedily — intervals overlapping or within `ENDPOINT_GAP` (10px) are extended:

```
if lo_new ≤ hi_prev + gap:
    merge intervals
```

The merged line sits at the **center of mass** of the cluster — the average signed perpendicular offset across all segment midpoints.

#### 2.4 Centerline Collapse (Parallel Stroke-Edge Pairing)

Thick drawn strokes in PDFs produce two edge contours (inner and outer ink boundaries). This stage identifies and collapses such pairs into single centerlines:

**Pairing criteria:**
1. Angle difference < 0.5°
2. Average perpendicular distance ≤ 15px (stroke thickness)
3. Projection overlap ratio ≥ 50%

**Collapse formula:**
```
center_start = (start_A + start_B) / 2
center_end   = (end_A   + end_B)   / 2
```

This typically reduces line counts by 5–10× while preserving architectural accuracy.

---

### 3. Spatial Dimension Linking (OCR → Graph Mapping)

**Module:** `lambda/raster_line_extraction/spatial_dimension_linker.py`

Maps AWS Textract OCR dimension annotations to their corresponding line segments using a **geometry-first matching pipeline**:

#### 3.1 KDTree Spatial Indexing

Line segment midpoints are indexed in a **KDTree** (from scipy) for O(log n) nearest-neighbor queries. For each OCR text block centroid, candidate lines are found within an adaptive search radius:

```
search_radius = max_distance + max_line_length × midpoint_tolerance
```

Brute-force O(n×m) fallback is used for small datasets (< 10 lines).

#### 3.2 Composite Scoring Function

Each (text, line) candidate is scored using a **weighted composite** of three geometric criteria:

```
score = 0.5 × distance_score + 0.3 × angle_score + 0.2 × midpoint_score
```

| Component | Formula | Weight |
|-----------|---------|--------|
| **Distance score** | `1.0 - (perp_dist / max_distance)` | 0.5 |
| **Angle score** | `1.0 - (angle_diff / max_angle_diff)` | 0.3 |
| **Midpoint score** | `1.0 - (|t_proj - 0.5| / (tolerance/2))` | 0.2 |

**Perpendicular distance** uses point-to-segment projection with endpoint clamping:

```
t = clamp(((p - s₁) · (s₂ - s₁)) / |s₂ - s₁|², 0, 1)
closest = s₁ + t × (s₂ - s₁)
distance = |p - closest|
```

**Angle alignment** considers both parallel and orthogonal orientations (architectural dimensions may be written at 0° or 90° relative to the measured line):

```
min(angular_dist(text_angle, line_angle),
    angular_dist(text_angle + 90°, line_angle),
    angular_dist(text_angle - 90°, line_angle))
```

#### 3.3 One-to-One Greedy Assignment

Matches are sorted by score (descending) and assigned greedily to enforce a **bijective mapping** — each text block maps to exactly one line, and each line receives at most one dimension label.

---

### 4. DXF Geometry Extraction (Vector Parsing)

**Module:** `lambda/dxf_geometry_extraction/geometry_extractor.py`

For vector-format files (DXF), geometric entities are parsed **deterministically** using ezdxf — no computer vision required:

| DXF Entity | Mathematical Mapping |
|------------|---------------------|
| `LINE` | One edge from (x₁, y₁) → (x₂, y₂) |
| `LWPOLYLINE` | N−1 sequential edges from consecutive vertices; closed polylines add a closing edge (last → first) |
| `MTEXT` | Text content + insertion point → dimension annotation with parsed numeric value and unit |

**Point deduplication** uses the same coordinate-rounding hash-map approach as the raster pipeline, with configurable precision (default: 6 decimal places = sub-micrometer for meter-scale drawings).

**Edge length filtering**: Zero-length edges from degenerate polylines or rounding artifacts are discarded (threshold: 10⁻⁹ drawing units).

Confidence for DXF-extracted data is always **1.0** (deterministic parse, no estimation).

---

### 5. Certified Mathematical Graph Construction

**Schema:** `schemas/certified-math-graph.schema.json`

The extracted coordinates are assembled into a **planar graph** G = (V, E, F) with three tiers of elements:

#### Nodes (Vertices)

Each unique coordinate point becomes a node with:
- Cartesian (x, y) or (x, y, z) coordinates
- Semantic classification: `wall-corner`, `wall-intersection`, `door-hinge`, `column-center`, etc.
- Degree (number of connected edges)
- Structural/material properties (load-bearing, concrete/steel/wood)

#### Edges

Each line segment becomes a directed or undirected edge with:
- References to `fromNodeId` and `toNodeId`
- **Weight** = Euclidean length in coordinate system units
- Semantic classification: `wall-segment`, `door-opening`, `beam`, `dimension-constraint`, etc.
- Thickness, fire rating, and material properties

#### Faces (Enclosed Regions)

Boundary edge rings define enclosed regions (rooms, corridors, stairwells) with:
- Ordered `boundaryEdgeIds` forming a closed polygon
- Computed **area** and **perimeter**
- Semantic classification: `room`, `corridor`, `stairwell`, `elevator-shaft`, etc.
- Building code properties: occupancy type, fire rating, means-of-egress designation

#### Graph Topology

The `topologyType` field certifies whether the graph is **planar** (no edge crossings) or **non-planar**, which is critical for spatial correctness validation.

---

### 6. Graph Certification & Integrity Checks

**Schema:** `schemas/certified-math-graph.schema.json` → `certification.checks`

Before a graph can be used for compliance analysis, it must pass a battery of **12 automated certification checks**:

| Check | Mathematical Method |
|-------|-------------------|
| `graph-connectivity` | Breadth-first/depth-first traversal to verify all nodes are reachable |
| `planarity-verification` | Kuratowski's theorem / Euler's formula: V − E + F = 2 for planar graphs |
| `duplicate-node-detection` | Coordinate rounding + hash-map collision detection |
| `duplicate-edge-detection` | Pairwise (from, to) node ID comparison |
| `dangling-edge-detection` | Node degree analysis — edges with unconnected endpoints |
| `face-closure-verification` | Boundary edge ring traversal confirming first == last vertex |
| `coordinate-consistency` | All coordinates within the declared coordinate system bounds |
| `dimensional-integrity` | Edge weights match Euclidean distance between node coordinates |
| `topology-completeness` | Every edge is part of at least one face boundary |
| `structural-continuity` | Load-bearing elements form a connected structural path |
| `spatial-consistency` | Face areas are positive; no self-overlapping faces |
| `self-intersection-detection` | Bentley-Ottmann sweep-line algorithm for edge-pair crossings |

A **SHA-256 checksum** of the canonical graph serialization is computed for tamper detection, and the entire certification is signed with the algorithm version, timestamp, and certifier identity.

---

### 7. Compliance Checking (Multi-Agent Pipeline)

**Module:** `services/compliance-checker/compliance_orchestrator.py`

The certified graph is evaluated against building codes using a **LangGraph multi-agent pipeline** with a critical architectural constraint: **agents are forbidden from performing mathematics**.

#### 7.1 Tool-Only Reasoning Architecture

Agents may ONLY access data through two tools — they cannot compute, estimate, or hallucinate measurements:

| Tool | Mathematical Operation |
|------|----------------------|
| `query_graph_for_distance(nodeA, nodeB)` | Direct edge weight lookup, or **BFS shortest-path** through certified edges |
| `query_qdrant_for_code(query)` | Vector similarity search over embedded building-code documents |

The BFS shortest-path algorithm traverses only **certified edges** (those with verified weights in the graph), ensuring all distance measurements are traceable to the mathematically validated graph:

```
BFS from nodeA:
  - Visit only edges with certified weights
  - Accumulate distance along the path
  - Return the minimum cumulative distance to nodeB
```

#### 7.2 Fan-Out / Fan-In Agent Topology

```
load_graph ──┬── fire_safety_agent ──┐
              └── accessibility_agent ─┤── critic_agent ── compile_report
```

1. **FireSafetyAgent**: Checks travel distances to exits, dead-end corridors, fire-rated assemblies, sprinkler coverage, exit widths — cites exact code sections from Qdrant
2. **AccessibilityAgent**: Checks door clear widths, corridor widths, ramp slopes, accessible route connectivity — cites ADA/IBC accessibility chapters
3. **CriticAgent**: Adversarial reviewer that attempts to **invalidate** every finding using rigorous logical analysis (circular reasoning, non sequitur, hallucinated codes, fabricated measurements)

#### 7.3 Anti-Hallucination Gate

Only findings that **survive critic review** are included in the final compliance report. The critic checks for:
- Cited codes that don't appear in tool output
- Measured values not returned by `query_graph_for_distance`
- Agent-performed arithmetic (instead of tool queries)
- Logical fallacies in the reasoning chain

The final `ComplianceReport` is validated through **Pydantic strict models** and conforms to the `compliance-report.schema.json` data contract.

---

## Pipeline Architecture

The system is orchestrated by an **AWS Step Functions state machine** (`statemachine/architectural-plan-processor.asl.json`) with:

- **5 processing stages** (Lambda functions)
- **5 Human-In-The-Loop (HITL) approval gates** using `.waitForTaskToken` callbacks
- **Exponential backoff retries** with jitter for transient failures
- **Centralized failure handling** with partial result archival
- **30-day maximum execution timeout** for long-running human reviews

```
ValidateUpload → HITL → CropZones → HITL → ExtractCoordinates → HITL → BuildMathGraph → HITL → GenerateComplianceReport → HITL → Finalize
```

---

## Data Contracts

All data flowing through the pipeline is governed by **JSON Schema (Draft 2020-12)** contracts:

| Schema | Description |
|--------|-------------|
| `uploaded-file.schema.json` | File upload with integrity checksums |
| `cropped-zones.schema.json` | Regions of interest with bounding boxes and OCR text |
| `raw-coordinates.schema.json` | Extracted points, lines, and dimension annotations in Cartesian coordinates |
| `certified-math-graph.schema.json` | Planar graph with nodes, edges, faces, and certification metadata |
| `compliance-report.schema.json` | Final compliance assessment with violations, statistics, and human approval |

---

## Project Structure

```
├── lambda/
│   ├── file_ingestion/              # File upload validation & integrity checks
│   ├── raster_line_extraction/      # CV pipeline: Canny → Hough → Consolidate → Link
│   │   ├── raster_line_extractor.py # 8-stage OpenCV pipeline
│   │   ├── line_consolidator.py     # Post-Hough fragment merging (5 stages)
│   │   └── spatial_dimension_linker.py # KDTree-based OCR-to-line matching
│   ├── dxf_geometry_extraction/     # DXF parser: LINE, LWPOLYLINE, MTEXT
│   │   └── geometry_extractor.py    # Deterministic vector extraction
│   └── graph_export/               # Graph → GeoJSON / IFC export
│       ├── graph_loader.py          # Schema validation & O(1) indexed access
│       ├── geojson_exporter.py      # GeoJSON Polygon output
│       └── ifc_exporter.py          # IFC (Industry Foundation Classes) output
├── services/
│   ├── compliance-checker/          # Multi-agent LangGraph compliance pipeline
│   │   └── compliance_orchestrator.py # Fire, Accessibility, Critic agents
│   └── dimension-audit/            # Java-based dimension auditing service
├── schemas/                         # JSON Schema data contracts (Draft 2020-12)
├── statemachine/                    # AWS Step Functions ASL definitions
├── frontend/                        # React + TypeScript + Vite UI
├── docker/                          # Dockerfiles for Python, Java, LangGraph
└── docker-compose.yml               # Local development environment
```

---

## License

Proprietary — All rights reserved.