"""
Custom exceptions for the Graph Export microservice.

Each exception carries structured context for CloudWatch logging and
downstream error routing.
"""

from __future__ import annotations

from typing import Any


class GraphExportError(Exception):
    """Base exception for all graph export errors."""

    def __init__(self, message: str, **context: Any) -> None:
        self.message = message
        self.context = context
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "errorType": type(self).__name__,
            "errorMessage": self.message,
            **self.context,
        }


class InvalidGraphError(GraphExportError):
    """Raised when the input JSON fails schema validation or integrity checks."""

    def __init__(
        self,
        message: str,
        *,
        graph_id: str | None = None,
        violations: list[str] | None = None,
    ) -> None:
        super().__init__(
            message,
            graphId=graph_id,
            violations=violations or [],
        )
        self.graph_id = graph_id
        self.violations = violations or []


class IFCExportError(GraphExportError):
    """Raised when IFC file generation fails."""

    def __init__(
        self,
        message: str,
        *,
        graph_id: str | None = None,
        wall_count: int = 0,
    ) -> None:
        super().__init__(
            message,
            graphId=graph_id,
            wallCount=wall_count,
        )
        self.graph_id = graph_id
        self.wall_count = wall_count


class GeoJSONExportError(GraphExportError):
    """Raised when GeoJSON generation fails."""

    def __init__(
        self,
        message: str,
        *,
        graph_id: str | None = None,
        feature_count: int = 0,
    ) -> None:
        super().__init__(
            message,
            graphId=graph_id,
            featureCount=feature_count,
        )
        self.graph_id = graph_id
        self.feature_count = feature_count