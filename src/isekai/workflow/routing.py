from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class WorkRoute(str, Enum):
    QUERY = "query"
    QUICK_CHANGE = "quick-change"
    UNIT = "unit"


ALLOWED_AGENT_LEVELS = {"L0", "L1"}
AGENT_ALLOWED_ACTIONS = {"read", "edit", "test"}
AGENT_PROHIBITED_ACTIONS = {
    "remote",
    "deploy",
    "credential-access",
    "promote",
    "decision",
}


@dataclass(frozen=True)
class RouteRequest:
    change: str
    risk: str
    ambiguous: bool = False
    multi_party: bool = False
    remote: bool = False
    sensitive: bool = False


@dataclass(frozen=True)
class RouteDecision:
    route: WorkRoute
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"route": self.route.value, "reasons": list(self.reasons)}


def classify_work(request: RouteRequest) -> RouteDecision:
    if request.change not in {"none", "local", "persistent"}:
        raise ValueError("change must be one of: none, local, persistent")
    if request.risk not in {"low", "high"}:
        raise ValueError("risk must be one of: low, high")
    reasons: list[str] = []
    if request.change == "persistent":
        reasons.append("persistent change")
    if request.risk == "high":
        reasons.append("high risk")
    if request.ambiguous:
        reasons.append("ambiguous acceptance criteria")
    if request.multi_party:
        reasons.append("multi-party decision")
    if request.remote:
        reasons.append("remote side effect")
    if request.sensitive:
        reasons.append("sensitive data or credentials")
    if reasons:
        return RouteDecision(WorkRoute.UNIT, tuple(reasons))
    if request.change == "none":
        return RouteDecision(WorkRoute.QUERY, ("no persistent change",))
    return RouteDecision(WorkRoute.QUICK_CHANGE, ("small reversible local change",))
