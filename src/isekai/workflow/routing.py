from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .errors import WorkflowError


class WorkRoute(str, Enum):
    QUERY = "query"
    QUICK_CHANGE = "quick-change"
    UNIT = "unit"


AGENT_LEVEL_ALLOWED_ACTIONS = {
    # L0 is the safe default for projects that only want analysis and planning.
    "L0": frozenset({"read"}),
    # L1 permits bounded local delivery after an approved Execution Envelope.
    "L1": frozenset({"read", "edit", "test"}),
}
ALLOWED_AGENT_LEVELS = set(AGENT_LEVEL_ALLOWED_ACTIONS)
AGENT_ALLOWED_ACTIONS = set().union(*AGENT_LEVEL_ALLOWED_ACTIONS.values())
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
        raise WorkflowError("change must be one of: none, local, persistent")
    if request.risk not in {"low", "high"}:
        raise WorkflowError("risk must be one of: low, high")
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
