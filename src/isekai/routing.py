"""Compatibility façade for work routing primitives."""

from .workflow.routing import (
    AGENT_ALLOWED_ACTIONS,
    AGENT_PROHIBITED_ACTIONS,
    ALLOWED_AGENT_LEVELS,
    RouteDecision,
    RouteRequest,
    WorkRoute,
    classify_work,
)

__all__ = ["RouteDecision", "RouteRequest", "WorkRoute", "classify_work"]
