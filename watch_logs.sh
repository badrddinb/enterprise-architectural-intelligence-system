#!/usr/bin/env bash
# =============================================================================
# watch_logs.sh — Dynamic Docker container log viewer
# =============================================================================
# Usage:
#   ./watch_logs.sh vector   → tails the vector (DXF geometry) container
#   ./watch_logs.sh raster   → tails the raster line extraction container
#   ./watch_logs.sh java     → tails the Java (dimension-audit) container
# =============================================================================

set -euo pipefail

# ── Validate exactly one argument ────────────────────────────────────────────
if [ $# -ne 1 ]; then
    echo "ERROR: Exactly one argument required."
    echo "Usage: $0 {vector|raster|java}"
    exit 1
fi

KEYWORD="$1"

# ── Validate the argument value ──────────────────────────────────────────────
if [[ "$KEYWORD" != "vector" && "$KEYWORD" != "raster" && "$KEYWORD" != "java" ]]; then
    echo "ERROR: Invalid argument '$KEYWORD'. Must be one of: vector, raster, java."
    echo "Usage: $0 {vector|raster|java}"
    exit 1
fi

# ── Query running containers and filter by keyword ───────────────────────────
CONTAINERS=$(docker ps --format '{{.Names}}' | grep -i "$KEYWORD" || true)
MATCH_COUNT=$(echo "$CONTAINERS" | grep -c . || true)

# ── Handle zero matches ──────────────────────────────────────────────────────
if [ "$MATCH_COUNT" -eq 0 ]; then
    echo "ERROR: No running container found matching keyword '$KEYWORD'."
    echo "Checked containers:"
    docker ps --format '{{.Names}}' | sed 's/^/  - /'
    exit 1
fi

# ── Handle multiple matches ──────────────────────────────────────────────────
if [ "$MATCH_COUNT" -gt 1 ]; then
    echo "ERROR: Multiple containers matched keyword '$KEYWORD' — cannot determine a unique target."
    echo "Matching containers:"
    echo "$CONTAINERS" | sed 's/^/  - /'
    echo "Refine the keyword or specify the container name directly."
    exit 1
fi

# ── Exactly one match — tail the logs ────────────────────────────────────────
CONTAINER_NAME=$(echo "$CONTAINERS" | head -n 1 | xargs)

echo "Tailing logs for container: $CONTAINER_NAME"
echo "────────────────────────────────────────────────────────────────────────────"

exec docker logs -f "$CONTAINER_NAME"