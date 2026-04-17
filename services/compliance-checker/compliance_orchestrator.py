"""
Compliance Checker Orchestrator — Multi-Agent LangGraph Pipeline

Evaluates a CertifiedMathGraph against building codes using a multi-agent
architecture that enforces tool-only reasoning to prevent hallucination.

Pipeline:
    load_graph → fan_out(fire_safety, accessibility) → critic → compile_report → END

Usage:
    python compliance_orchestrator.py --graph path/to/graph.json
"""

from __future__ import annotations

import argparse
import json
import math
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

# ---------------------------------------------------------------------------
# Pydantic models — strict output enforcement
# ---------------------------------------------------------------------------
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# LangChain / LangGraph imports
# ---------------------------------------------------------------------------
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool as lc_tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

# ---------------------------------------------------------------------------
# Qdrant client (injected at runtime)
# ---------------------------------------------------------------------------
from qdrant_client import QdrantClient


# ===========================================================================
# 1.  PYDANTIC MODELS
# ===========================================================================


class Severity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class Violation(BaseModel):
    """A single, validated compliance violation — the unit of output."""

    violation_type: str = Field(
        ...,
        description="Category of violation (e.g. 'fire-safety', 'accessibility', 'egress').",
    )
    severity: Severity = Field(
        ...,
        description="Severity level of the violation.",
    )
    node_id: str = Field(
        ...,
        description="UUID of the graph element (node / edge / face) where the violation is located.",
    )
    cited_code: str = Field(
        ...,
        description="The building-code section reference (e.g. 'IBC 1005.1', 'ADA 404.2.3').",
    )
    description: str = Field(
        default="",
        description="Human-readable explanation of the violation.",
    )
    measured_value: Optional[float] = Field(
        default=None,
        description="The measured numeric value from the plan.",
    )
    required_value: Optional[float] = Field(
        default=None,
        description="The code-required threshold value.",
    )
    unit: Optional[str] = Field(
        default=None,
        description="Unit of measurement (e.g. 'm', 'mm', 'ft').",
    )
    survived_criticism: bool = Field(
        default=True,
        description="Whether this finding survived the CriticAgent's adversarial review.",
    )


class ComplianceReport(BaseModel):
    """
    Strict, final output of the LangGraph pipeline.
    Every violation has been validated by the CriticAgent and conforms to
    the ComplianceReport data contract.
    """

    report_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    graph_id: str = Field(..., description="Reference to CertifiedMathGraph.graphId.")
    source_coordinates_id: str = Field(
        default="", description="Transitive lineage to RawCoordinates."
    )
    source_file_id: str = Field(default="", description="Transitive lineage to UploadedFile.")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    overall_status: Literal["compliant", "non-compliant", "conditionally-compliant", "pending-review"] = Field(
        default="pending-review",
    )
    violations: list[Violation] = Field(
        default_factory=list,
        description="Consolidated violations that survived critic review.",
    )
    total_rules_evaluated: int = Field(default=0, ge=0)
    rules_passed: int = Field(default=0, ge=0)
    rules_failed: int = Field(default=0, ge=0)

    @property
    def compliance_score(self) -> float:
        if self.total_rules_evaluated == 0:
            return 100.0
        return round(
            (self.rules_passed / self.total_rules_evaluated) * 100, 2
        )


# ===========================================================================
# 2.  INTERNAL STATE TYPES  (LangGraph graph state)
# ===========================================================================


class AgentFinding(TypedDict):
    """Intermediate finding produced by an analyst agent."""

    violation_type: str
    severity: str
    node_id: str
    cited_code: str
    description: str
    measured_value: Optional[float]
    required_value: Optional[float]
    unit: Optional[str]
    tool_calls_used: list[str]  # traceability — which tools were invoked


class CriticVerdict(TypedDict):

    finding_index: int
    is_valid: bool
    reason: str
    fallacy_type: Optional[str]  # e.g. "hallucinated_code", "unstated_premise"


class GraphState(TypedDict):
    """
    Mutable state that flows through every node in the LangGraph pipeline.
    """

    # -- input --
    certified_math_graph: dict[str, Any]
    graph_id: str

    # -- parsed graph data (populated by load_graph) --
    nodes: dict[str, dict]  # nodeId → node data
    edges: dict[str, dict]  # edgeId → edge data
    faces: dict[str, dict]  # faceId → face data
    adjacency: dict[str, list[str]]  # nodeId → [connected nodeIds]
    edge_weights: dict[str, float]  # "nodeA|nodeB" → certified distance

    # -- agent outputs --
    fire_safety_findings: list[AgentFinding]
    accessibility_findings: list[AgentFinding]

    # -- critic outputs --
    critic_verdicts: list[CriticVerdict]

    # -- final output --
    compliance_report: Optional[dict]  # serialised ComplianceReport

    # -- metadata --
    errors: list[str]


# ===========================================================================
# 3.  TOOLS  — the ONLY way agents may access data or codes
# ===========================================================================

# Module-level references set during initialisation
_qdrant_client: Optional[QdrantClient] = None
_graph_state_ref: Optional[GraphState] = None
_qdrant_collection: str = "building_codes"


def initialise_tools(
    qdrant_client: QdrantClient,
    collection_name: str = "building_codes",
) -> None:
    """Inject runtime dependencies into the tool closures."""
    global _qdrant_client, _qdrant_collection  # noqa: PLW0603
    _qdrant_client = qdrant_client
    _qdrant_collection = collection_name


def set_graph_state(state: GraphState) -> None:
    """Set the current graph state for tool access."""
    global _graph_state_ref  # noqa: PLW0603
    _graph_state_ref = state


@lc_tool
def query_qdrant_for_code(query_string: str) -> str:
    """
    Search the Qdrant vector database for building-code sections relevant
    to the given query.

    Returns the raw retrieved text chunks.  The agent MUST cite these
    exact references — it is NOT permitted to invent or paraphrase codes.
    """
    if _qdrant_client is None:
        return "ERROR: Qdrant client not initialised. Call initialise_tools() first."

    try:
        results = _qdrant_client.query_points(
            collection_name=_qdrant_collection,
            query_text=query_string,
            limit=5,
        )
        if not results.points:
            return "No relevant code sections found in the vector database."

        snippets: list[str] = []
        for point in results.points:
            payload = point.payload or {}
            code_ref = payload.get("code_reference", "UNKNOWN")
            text = payload.get("text", "")
            source = payload.get("source", "")
            snippets.append(f"[{code_ref}] ({source}): {text}")

        return "\n\n---\n\n".join(snippets)

    except Exception as exc:
        return f"ERROR querying Qdrant: {exc}"


@lc_tool
def query_graph_for_distance(node_a: str, node_b: str) -> str:
    """
    Return the certified distance between two nodes in the mathematical graph.

    The distance is derived from the certified graph data — NOT computed by
    the agent.  Agents MUST use this tool for all distance measurements;
    they are NOT permitted to perform calculations themselves.
    """
    if _graph_state_ref is None:
        return "ERROR: Graph state not set."

    edge_weights = _graph_state_ref.get("edge_weights", {})
    adjacency = _graph_state_ref.get("adjacency", {})
    nodes = _graph_state_ref.get("nodes", {})

    # Direct edge lookup
    key_forward = f"{node_a}|{node_b}"
    key_reverse = f"{node_b}|{node_a}"

    if key_forward in edge_weights:
        return json.dumps({
            "distance": edge_weights[key_forward],
            "unit": "coordinate_units",
            "method": "direct_edge_weight",
            "certified": True,
        })

    if key_reverse in edge_weights:
        return json.dumps({
            "distance": edge_weights[key_reverse],
            "unit": "coordinate_units",
            "method": "direct_edge_weight",
            "certified": True,
        })

    # Compute shortest path (BFS) through certified edges if no direct edge
    if node_a in nodes and node_b in nodes:
        path_distance = _bfs_shortest_path_distance(node_a, node_b, adjacency, edge_weights)
        if path_distance is not None:
            return json.dumps({
                "distance": path_distance,
                "unit": "coordinate_units",
                "method": "shortest_path_through_certified_edges",
                "certified": True,
            })

    return json.dumps({
        "distance": None,
        "error": f"No certified path found between {node_a} and {node_b}.",
        "certified": False,
    })


def _bfs_shortest_path_distance(
    start: str,
    target: str,
    adjacency: dict[str, list[str]],
    edge_weights: dict[str, float],
) -> Optional[float]:
    """BFS shortest-path distance through certified edges only."""
    from collections import deque

    visited: set[str] = {start}
    queue: deque[tuple[str, float]] = deque([(start, 0.0)])

    while queue:
        current, dist_so_far = queue.popleft()
        for neighbor in adjacency.get(current, []):
            if neighbor in visited:
                continue
            key = f"{current}|{neighbor}"
            weight = edge_weights.get(key, edge_weights.get(f"{neighbor}|{current}"))
            if weight is None:
                continue  # skip uncertified edges
            new_dist = dist_so_far + weight
            if neighbor == target:
                return new_dist
            visited.add(neighbor)
            queue.append((neighbor, new_dist))
    return None


# Collect tool definitions for binding to agents
AGENT_TOOLS = [query_qdrant_for_code, query_graph_for_distance]


# ===========================================================================
# 4.  SYSTEM PROMPTS
# ===========================================================================

FIRE_SAFETY_SYSTEM_PROMPT = """\
You are the FireSafetyAgent — a specialist in fire-code compliance analysis.

## YOUR MISSION
Analyse the CertifiedMathGraph for violations of fire safety building codes
(e.g. IBC Chapter 10 — Means of Egress, NFPA 101 — Life Safety Code,
IFC — International Fire Code).

## WHAT YOU MUST CHECK
- Travel distance to exits (max distance per occupancy type)
- Dead-end corridor lengths
- Fire-rated wall/door assemblies and their ratings
- Sprinkler coverage areas
- Exit widths and capacities
- Fire-separated area limits

## ABSOLUTE RULES — VIOLATION IS GROUNDS FOR SYSTEM REJECTION
1. **NO MATH**: You MUST NOT compute, calculate, or estimate any distances,
   areas, or measurements. You MUST use the `query_graph_for_distance` tool
   for EVERY measurement. If you cannot obtain a measurement via the tool,
   state "MEASUREMENT_UNAVAILABLE" — never fabricate a number.

2. **NO HALLUCINATED CODES**: You MUST use `query_qdrant_for_code` to look up
   EVERY code section you cite. You may ONLY cite code references returned by
   the tool. If the tool does not return a relevant code, do NOT invent one.

3. **TOOL-ONLY REASONING**: Every finding MUST be traceable to tool output.
   Include the tool call results in your reasoning.

## OUTPUT FORMAT
For each finding, output a JSON block:
```json
{
  "violation_type": "fire-safety",
  "severity": "critical|major|minor",
  "node_id": "<UUID of affected element>",
  "cited_code": "<exact code reference from tool>",
  "description": "<clear description referencing tool-returned data>",
  "measured_value": <number or null>,
  "required_value": <number or null>,
  "unit": "<unit string or null>",
  "tool_calls_used": ["<list of tool calls that produced this finding>"]
}
```

If the plan is fully compliant for fire safety, return an empty list.
"""

ACCESSIBILITY_SYSTEM_PROMPT = """\
You are the AccessibilityAgent — a specialist in ADA and accessibility-code
compliance analysis.

## YOUR MISSION
Analyse the CertifiedMathGraph for violations of accessibility codes
(e.g. ADA Standards for Accessible Design, IBC Chapter 11 — Accessibility).

## WHAT YOU MUST CHECK
- Door clear widths (minimum 32 inches / 815 mm)
- Corridor and pathway widths
- Ramp slopes and landing dimensions
- Accessible route connectivity
- Turning space requirements
- Reach range compliance

## ABSOLUTE RULES — VIOLATION IS GROUNDS FOR SYSTEM REJECTION
1. **NO MATH**: You MUST NOT compute, calculate, or estimate any distances,
   widths, slopes, or measurements. You MUST use the `query_graph_for_distance`
   tool for EVERY measurement. If you cannot obtain a measurement via the tool,
   state "MEASUREMENT_UNAVAILABLE" — never fabricate a number.

2. **NO HALLUCINATED CODES**: You MUST use `query_qdrant_for_code` to look up
   EVERY code section you cite. You may ONLY cite code references returned by
   the tool. If the tool does not return a relevant code, do NOT invent one.

3. **TOOL-ONLY REASONING**: Every finding MUST be traceable to tool output.
   Include the tool call results in your reasoning.

## OUTPUT FORMAT
For each finding, output a JSON block:
```json
{
  "violation_type": "accessibility",
  "severity": "critical|major|minor",
  "node_id": "<UUID of affected element>",
  "cited_code": "<exact code reference from tool>",
  "description": "<clear description referencing tool-returned data>",
  "measured_value": <number or null>,
  "required_value": <number or null>,
  "unit": "<unit string or null>",
  "tool_calls_used": ["<list of tool calls that produced this finding>"]
}
```

If the plan is fully compliant for accessibility, return an empty list.
"""

CRITIC_SYSTEM_PROMPT = """\
You are the CriticAgent — an adversarial reviewer whose sole purpose is to
INVALIDATE the findings of the FireSafetyAgent and AccessibilityAgent.

## YOUR MISSION
Review EVERY finding and attempt to discredit it using rigorous logical
analysis. You are the last line of defence against hallucinated, illogical,
or unsubstantiated violations.

## INVALIDATION CRITERIA

You MUST mark a finding as INVALID if ANY of the following apply:

### Logical Fallacies
- **Circular Reasoning**: The finding's evidence assumes its own conclusion.
- **Non Sequitur**: The conclusion does not follow from the evidence presented.
- **False Analogy**: The finding compares dissimilar situations as equivalent.
- **Appeal to Ignorance**: "It must be a violation because we can't prove it isn't."
- **Hasty Generalisation**: Drawing a conclusion from insufficient evidence.

### Hallucination Indicators
- The cited_code does NOT appear in the tool output.
- The measured_value does NOT match any value returned by query_graph_for_distance.
- The finding references a node_id that doesn't exist in the graph.
- The description contains specific numbers not traceable to tool output.
- The agent performed arithmetic (e.g., subtraction of coordinates) instead of
  using query_graph_for_distance.

### Methodological Errors
- The finding relies on reasoning rather than tool output.
- The agent inferred a measurement instead of querying for it.
- The severity is inconsistent with the code requirement.
- The violation_type doesn't match the agent's domain.

## OUTPUT FORMAT
For EACH finding, provide:
```json
{
  "finding_index": <0-based index in the combined findings list>,
  "is_valid": true|false,
  "reason": "<detailed explanation of why valid or invalid>",
  "fallacy_type": "<one of: circular_reasoning, non_sequitur, false_analogy, appeal_to_ignorance, hasty_generalisation, hallucinated_code, hallucinated_measurement, fabricated_data, methodological_error, none>"
}
```

Be ruthless. It is better to let a real violation pass than to certify a
hallucinated one. When in doubt, mark INVALID.
"""


# ===========================================================================
# 5.  GRAPH CONSTRUCTION HELPERS
# ===========================================================================


def parse_certified_graph(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Parse the raw CertifiedMathGraph JSON into indexed lookup structures.
    """
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    faces: dict[str, dict] = {}
    adjacency: dict[str, list[str]] = {}
    edge_weights: dict[str, float] = {}

    for node in raw.get("nodes", []):
        nid = node["nodeId"]
        nodes[nid] = node
        adjacency.setdefault(nid, [])

    for edge in raw.get("edges", []):
        eid = edge["edgeId"]
        edges[eid] = edge
        src = edge["fromNodeId"]
        tgt = edge["toNodeId"]

        # Build adjacency (undirected)
        if tgt not in adjacency.get(src, []):
            adjacency.setdefault(src, []).append(tgt)
        if src not in adjacency.get(tgt, []):
            adjacency.setdefault(tgt, []).append(src)

        # Store certified weight (distance)
        weight = edge.get("weight")
        if weight is not None:
            edge_weights[f"{src}|{tgt}"] = weight
        else:
            # Derive from coordinates if weight is absent
            src_node = nodes.get(src)
            tgt_node = nodes.get(tgt)
            if src_node and tgt_node:
                dx = (src_node.get("x", 0) or 0) - (tgt_node.get("x", 0) or 0)
                dy = (src_node.get("y", 0) or 0) - (tgt_node.get("y", 0) or 0)
                edge_weights[f"{src}|{tgt}"] = math.sqrt(dx * dx + dy * dy)

    for face in raw.get("faces", []):
        fid = face["faceId"]
        faces[fid] = face

    return {
        "nodes": nodes,
        "edges": edges,
        "faces": faces,
        "adjacency": adjacency,
        "edge_weights": edge_weights,
    }


def _findings_from_text(text: str) -> list[AgentFinding]:
    """Best-effort extraction of JSON finding blocks from LLM text output."""
    findings: list[AgentFinding] = []
    # Try to find JSON blocks in the text
    try:
        # Attempt direct JSON parse first
        data = json.loads(text)
        if isinstance(data, list):
            return [_normalise_finding(f, idx) for idx, f in enumerate(data)]
        if isinstance(data, dict):
            if "findings" in data:
                return [_normalise_finding(f, idx) for idx, f in enumerate(data["findings"])]
            return [_normalise_finding(data, 0)]
    except json.JSONDecodeError:
        pass

    # Fallback: extract ```json ... ``` blocks
    import re

    json_blocks = re.findall(r"```json\s*(.*?)```", text, re.DOTALL)
    if not json_blocks:
        # Try to find bare JSON objects
        json_blocks = re.findall(r"\{[^{}]*" r"(?:\{[^{}]*\}[^{}]*)*" r"\}", text, re.DOTALL)

    for block in json_blocks:
        try:
            data = json.loads(block.strip())
            findings.append(_normalise_finding(data, len(findings)))
        except (json.JSONDecodeError, KeyError):
            continue

    return findings


def _normalise_finding(data: dict, index: int) -> AgentFinding:
    """Normalise a raw dict into an AgentFinding."""
    return AgentFinding(
        violation_type=data.get("violation_type", "unknown"),
        severity=data.get("severity", "minor"),
        node_id=data.get("node_id", ""),
        cited_code=data.get("cited_code", ""),
        description=data.get("description", ""),
        measured_value=data.get("measured_value"),
        required_value=data.get("required_value"),
        unit=data.get("unit"),
        tool_calls_used=data.get("tool_calls_used", []),
    )


def _verdicts_from_text(text: str, total_findings: int) -> list[CriticVerdict]:
    """Extract critic verdicts from LLM output."""
    verdicts: list[CriticVerdict] = []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            for v in data:
                verdicts.append(
                    CriticVerdict(
                        finding_index=v.get("finding_index", len(verdicts)),
                        is_valid=v.get("is_valid", False),
                        reason=v.get("reason", ""),
                        fallacy_type=v.get("fallacy_type"),
                    )
                )
            return verdicts
        if isinstance(data, dict):
            if "verdicts" in data:
                for v in data["verdicts"]:
                    verdicts.append(
                        CriticVerdict(
                            finding_index=v.get("finding_index", len(verdicts)),
                            is_valid=v.get("is_valid", False),
                            reason=v.get("reason", ""),
                            fallacy_type=v.get("fallacy_type"),
                        )
                    )
                return verdicts
    except json.JSONDecodeError:
        pass

    # Fallback: extract JSON blocks
    import re

    json_blocks = re.findall(r"```json\s*(.*?)```", text, re.DOTALL)
    if not json_blocks:
        json_blocks = re.findall(r"\{[^{}]*" r"(?:\{[^{}]*\}[^{}]*)*" r"\}", text, re.DOTALL)

    for block in json_blocks:
        try:
            v = json.loads(block.strip())
            verdicts.append(
                CriticVerdict(
                    finding_index=v.get("finding_index", len(verdicts)),
                    is_valid=v.get("is_valid", False),
                    reason=v.get("reason", ""),
                    fallacy_type=v.get("fallacy_type"),
                )
            )
        except (json.JSONDecodeError, KeyError):
            continue

    # Default: if we couldn't parse any verdicts, invalidate everything
    if not verdicts:
        for i in range(total_findings):
            verdicts.append(
                CriticVerdict(
                    finding_index=i,
                    is_valid=False,
                    reason="Critic output could not be parsed — defaulting to invalid for safety.",
                    fallacy_type="methodological_error",
                )
            )

    return verdicts


# ===========================================================================
# 6.  LANGGRAPH NODE FUNCTIONS
# ===========================================================================


def node_load_graph(state: GraphState) -> dict:
    """
    Parse the CertifiedMathGraph JSON into indexed lookup structures
    needed by the tools and agents.
    """
    raw = state["certified_math_graph"]
    parsed = parse_certified_graph(raw)

    return {
        "nodes": parsed["nodes"],
        "edges": parsed["edges"],
        "faces": parsed["faces"],
        "adjacency": parsed["adjacency"],
        "edge_weights": parsed["edge_weights"],
        "graph_id": raw.get("graphId", state.get("graph_id", "")),
        "fire_safety_findings": [],
        "accessibility_findings": [],
        "critic_verdicts": [],
        "errors": [],
    }


def _run_agent_with_tools(
    llm: BaseChatModel,
    system_prompt: str,
    graph_summary: str,
    agent_name: str,
) -> str:
    """
    Run a single agent with tool-calling loop.
    The agent is given tools and must use them — it cannot do math itself.
    """
    llm_with_tools = llm.bind_tools(AGENT_TOOLS)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=(
                f"Analyse the following CertifiedMathGraph for {agent_name} compliance.\n\n"
                f"## Graph Summary\n{graph_summary}\n\n"
                "Produce your findings as JSON. Remember: use the tools for ALL "
                "measurements and code lookups."
            )
        ),
    ]

    # Tool-calling loop (max 15 iterations to prevent runaway)
    max_iterations = 15
    for _ in range(max_iterations):
        response = llm_with_tools.invoke(messages)

        # If no tool calls, the agent is done — return its text
        if not response.tool_calls:
            return response.content or ""

        # Process tool calls
        messages.append(response)
        for tc in response.tool_calls:
            tool_name = tc["name"]
            tool_args = tc["args"]

            # Dispatch to the actual tool function
            if tool_name == "query_qdrant_for_code":
                result = query_qdrant_for_code.invoke(tool_args)
            elif tool_name == "query_graph_for_distance":
                result = query_graph_for_distance.invoke(tool_args)
            else:
                result = f"ERROR: Unknown tool {tool_name}"

            messages.append(
                ToolMessage(content=str(result), tool_call_id=tc["id"])
            )

    return "ERROR: Agent exceeded maximum tool-calling iterations."


def _build_graph_summary(state: GraphState) -> str:
    """Build a concise text summary of the graph for agent prompts."""
    nodes = state.get("nodes", {})
    edges = state.get("edges", {})
    faces = state.get("faces", {})

    # Categorise nodes by type
    node_type_counts: dict[str, int] = {}
    for n in nodes.values():
        ntype = n.get("nodeType", "other")
        node_type_counts[ntype] = node_type_counts.get(ntype, 0) + 1

    # Categorise edges by type
    edge_type_counts: dict[str, int] = {}
    for e in edges.values():
        etype = e.get("edgeType", "other")
        edge_type_counts[etype] = edge_type_counts.get(etype, 0) + 1

    # Categorise faces by type
    face_type_counts: dict[str, int] = {}
    for f in faces.values():
        ftype = f.get("faceType", "other")
        face_type_counts[ftype] = face_type_counts.get(ftype, 0) + 1

    # List faces with properties relevant to compliance
    face_details: list[str] = []
    for fid, f in faces.items():
        props = f.get("properties", {})
        parts = [
            f"Face {fid}: type={f.get('faceType', 'unknown')}",
            f"area={f.get('area', 'N/A')}",
        ]
        if f.get("perimeter"):
            parts.append(f"perimeter={f['perimeter']}")
        if props.get("occupancyType"):
            parts.append(f"occupancy={props['occupancyType']}")
        if props.get("isFireRated") is not None:
            parts.append(f"fireRated={props['isFireRated']}")
        if props.get("isMeansOfEgress") is not None:
            parts.append(f"meansOfEgress={props['isMeansOfEgress']}")
        face_details.append(" | ".join(parts))

    # List edges with fire ratings
    fire_rated_edges: list[str] = []
    for eid, e in edges.items():
        props = e.get("properties", {})
        if props.get("fireRating") is not None:
            fire_rated_edges.append(
                f"Edge {eid}: type={e.get('edgeType')}, "
                f"fireRating={props['fireRating']}min, "
                f"thickness={e.get('thickness', 'N/A')}"
            )

    summary_parts = [
        f"Graph ID: {state.get('graph_id', 'unknown')}",
        f"Nodes: {len(nodes)} — types: {node_type_counts}",
        f"Edges: {len(edges)} — types: {edge_type_counts}",
        f"Faces: {len(faces)} — types: {face_type_counts}",
    ]

    if face_details:
        summary_parts.append("\nFace details:\n" + "\n".join(f"  - {fd}" for fd in face_details))
    if fire_rated_edges:
        summary_parts.append(
            "\nFire-rated edges:\n" + "\n".join(f"  - {fe}" for fe in fire_rated_edges)
        )

    return "\n".join(summary_parts)


# --- Node factory helpers (to inject the LLM) ---


def make_fire_safety_node(llm: BaseChatModel):
    """Create the FireSafetyAgent node function with injected LLM."""

    def node_fire_safety(state: GraphState) -> dict:
        set_graph_state(state)
        graph_summary = _build_graph_summary(state)
        raw_output = _run_agent_with_tools(
            llm=llm,
            system_prompt=FIRE_SAFETY_SYSTEM_PROMPT,
            graph_summary=graph_summary,
            agent_name="fire safety",
        )
        findings = _findings_from_text(raw_output)
        return {"fire_safety_findings": findings}

    return node_fire_safety


def make_accessibility_node(llm: BaseChatModel):
    """Create the AccessibilityAgent node function with injected LLM."""

    def node_accessibility(state: GraphState) -> dict:
        set_graph_state(state)
        graph_summary = _build_graph_summary(state)
        raw_output = _run_agent_with_tools(
            llm=llm,
            system_prompt=ACCESSIBILITY_SYSTEM_PROMPT,
            graph_summary=graph_summary,
            agent_name="accessibility",
        )
        findings = _findings_from_text(raw_output)
        return {"accessibility_findings": findings}

    return node_accessibility


def make_critic_node(llm: BaseChatModel):
    """Create the CriticAgent node function with injected LLM."""

    def node_critic(state: GraphState) -> dict:
        fire_findings = state.get("fire_safety_findings", [])
        access_findings = state.get("accessibility_findings", [])
        all_findings = fire_findings + access_findings

        if not all_findings:
            return {"critic_verdicts": []}

        # Build the critic prompt with all findings
        findings_text = json.dumps(all_findings, indent=2)
        messages = [
            SystemMessage(content=CRITIC_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Review the following {len(all_findings)} findings from the "
                    f"FireSafetyAgent (indices 0–{len(fire_findings) - 1}) and "
                    f"AccessibilityAgent (indices {len(fire_findings)}–{len(all_findings) - 1}).\n\n"
                    f"## Findings to Validate\n```json\n{findings_text}\n```\n\n"
                    "For EACH finding, provide a verdict JSON block. "
                    "Be thorough and ruthless."
                )
            ),
        ]

        response = llm.invoke(messages)
        verdicts = _verdicts_from_text(response.content or "", len(all_findings))
        return {"critic_verdicts": verdicts}

    return node_critic


def node_compile_report(state: GraphState) -> dict:
    """
    Final node: merge findings that survived criticism into a strict
    Pydantic ComplianceReport.  This is the anti-hallucination gate.
    """
    fire_findings = state.get("fire_safety_findings", [])
    access_findings = state.get("accessibility_findings", [])
    all_findings = fire_findings + access_findings
    verdicts = state.get("critic_verdicts", [])

    # Build lookup: finding_index → is_valid
    validity_map: dict[int, bool] = {}
    for v in verdicts:
        validity_map[v["finding_index"]] = v["is_valid"]

    # Filter: only keep findings that survived criticism
    validated_violations: list[Violation] = []
    for idx, finding in enumerate(all_findings):
        if not validity_map.get(idx, False):
            # Finding was invalidated by critic — skip
            continue

        try:
            violation = Violation(
                violation_type=finding.get("violation_type", "unknown"),
                severity=Severity(finding.get("severity", "minor")),
                node_id=finding.get("node_id", ""),
                cited_code=finding.get("cited_code", ""),
                description=finding.get("description", ""),
                measured_value=finding.get("measured_value"),
                required_value=finding.get("required_value"),
                unit=finding.get("unit"),
                survived_criticism=True,
            )
            validated_violations.append(violation)
        except Exception:
            # Pydantic validation failed — discard this finding
            continue

    # Determine overall status
    critical_count = sum(1 for v in validated_violations if v.severity == Severity.CRITICAL)
    major_count = sum(1 for v in validated_violations if v.severity == Severity.MAJOR)

    if critical_count > 0:
        overall_status: str = "non-compliant"
    elif major_count > 0:
        overall_status = "conditionally-compliant"
    elif validated_violations:
        overall_status = "conditionally-compliant"
    else:
        overall_status = "compliant"

    # Build and validate the final report through Pydantic
    report = ComplianceReport(
        graph_id=state.get("graph_id", ""),
        source_coordinates_id=state.get("certified_math_graph", {}).get(
            "sourceCoordinatesId", ""
        ),
        source_file_id=state.get("certified_math_graph", {}).get("sourceFileId", ""),
        overall_status=overall_status,  # type: ignore[arg-type]
        violations=validated_violations,
        total_rules_evaluated=max(len(all_findings), 1),
        rules_passed=max(len(all_findings) - len(validated_violations), 0),
        rules_failed=len(validated_violations),
    )

    return {"compliance_report": report.model_dump(mode="json")}


# ===========================================================================
# 7.  LANGGRAPH STATE GRAPH ASSEMBLY
# ===========================================================================


def build_compliance_graph(
    llm: BaseChatModel,
    qdrant_client: QdrantClient,
    qdrant_collection: str = "building_codes",
) -> CompiledStateGraph:
    """
    Construct and compile the LangGraph StateGraph for compliance checking.

    Parameters
    ----------
    llm : BaseChatModel
        A LangChain-compatible chat model (e.g. ChatOpenAI).
    qdrant_client : QdrantClient
        An initialised Qdrant client connected to the building-codes collection.
    qdrant_collection : str
        Name of the Qdrant collection containing vectorised building codes.

    Returns
    -------
    CompiledStateGraph
        A compiled LangGraph ready for invocation.
    """
    initialise_tools(qdrant_client, qdrant_collection)

    builder = StateGraph(GraphState)

    # -- Add nodes --
    builder.add_node("load_graph", node_load_graph)
    builder.add_node("fire_safety_agent", make_fire_safety_node(llm))
    builder.add_node("accessibility_agent", make_accessibility_node(llm))
    builder.add_node("critic_agent", make_critic_node(llm))
    builder.add_node("compile_report", node_compile_report)

    # -- Define edges --
    builder.add_edge(START, "load_graph")

    # Fan-out: after loading, run both agents in parallel
    builder.add_edge("load_graph", "fire_safety_agent")
    builder.add_edge("load_graph", "accessibility_agent")

    # Fan-in: both agents must complete before critic runs
    builder.add_edge("fire_safety_agent", "critic_agent")
    builder.add_edge("accessibility_agent", "critic_agent")

    # Critic → final report
    builder.add_edge("critic_agent", "compile_report")
    builder.add_edge("compile_report", END)

    return builder.compile()


# ===========================================================================
# 8.  MAIN — CLI entry point
# ===========================================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the multi-agent compliance checker on a CertifiedMathGraph."
    )
    parser.add_argument(
        "--graph",
        required=True,
        help="Path to the CertifiedMathGraph JSON file.",
    )
    parser.add_argument(
        "--qdrant-url",
        default="http://localhost:6333",
        help="URL of the Qdrant vector database.",
    )
    parser.add_argument(
        "--qdrant-collection",
        default="building_codes",
        help="Name of the Qdrant collection for building codes.",
    )
    parser.add_argument(
        "--llm-model",
        default="gpt-4o",
        help="LangChain model name to use for agents.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write the compliance report JSON. Prints to stdout if omitted.",
    )

    args = parser.parse_args()

    # --- Load graph ---
    with open(args.graph, encoding="utf-8") as f:
        certified_math_graph = json.load(f)

    # --- Initialise clients ---
    from qdrant_client import QdrantClient as _QC

    qdrant = _QC(url=args.qdrant_url)

    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model=args.llm_model, temperature=0)

    # --- Build and run graph ---
    graph = build_compliance_graph(llm, qdrant, args.qdrant_collection)

    initial_state: GraphState = {
        "certified_math_graph": certified_math_graph,
        "graph_id": certified_math_graph.get("graphId", ""),
        "nodes": {},
        "edges": {},
        "faces": {},
        "adjacency": {},
        "edge_weights": {},
        "fire_safety_findings": [],
        "accessibility_findings": [],
        "critic_verdicts": [],
        "compliance_report": None,
        "errors": [],
    }

    result = graph.invoke(initial_state)

    # --- Output ---
    report_data = result.get("compliance_report")
    if report_data is None:
        print("ERROR: No compliance report was generated.")
        return

    # Validate through Pydantic one final time
    report = ComplianceReport.model_validate(report_data)
    output_json = report.model_dump_json(indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        print(f"Compliance report written to {args.output}")
    else:
        print(output_json)

    # Print summary
    print(f"\n{'='*60}")
    print(f"COMPLIANCE SUMMARY")
    print(f"{'='*60}")
    print(f"  Status:     {report.overall_status}")
    print(f"  Score:      {report.compliance_score}%")
    print(f"  Violations: {len(report.violations)}")
    print(f"  Critical:   {sum(1 for v in report.violations if v.severity == Severity.CRITICAL)}")
    print(f"  Major:      {sum(1 for v in report.violations if v.severity == Severity.MAJOR)}")
    print(f"  Minor:      {sum(1 for v in report.violations if v.severity == Severity.MINOR)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()