from __future__ import annotations

import re
from typing import Any, Mapping

from .workflow import RouteDecision, RouteRequest, WorkRoute, classify_work


INTAKE_SOURCES = {"host-goal", "direct-request"}
CHANGE_VALUES = {"none", "local", "persistent"}
RISK_VALUES = {"low", "high"}


def _text(value: Any, field: str, *, required: bool = False) -> str:
    if value is None:
        if required:
            raise ValueError(f"intake field must be non-empty: {field}")
        return ""
    if not isinstance(value, str) or not value.strip():
        if required:
            raise ValueError(f"intake field must be non-empty: {field}")
        return ""
    return value.strip()


def _strings(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"intake field must be a list of non-empty strings: {field}")
    return [item.strip() for item in value]


def _infer_change(text: str, source: str) -> str:
    if source == "host-goal":
        return "persistent"
    lowered = text.lower()
    question_markers = ("?", "무엇", "뭐가", "왜", "어떻게", "설명", "what ", "why ", "how ")
    change_markers = (
        "개발",
        "추가",
        "수정",
        "변경",
        "구현",
        "만들",
        "리팩터",
        "배포",
        "삭제",
        "fix",
        "add ",
        "change ",
        "implement",
        "build ",
        "refactor",
        "deploy",
        "delete",
    )
    quick_markers = ("오타", "typo", "문구", "format", "whitespace")
    if any(marker in lowered for marker in question_markers) and not any(
        marker in lowered for marker in change_markers
    ):
        return "none"
    if any(marker in lowered for marker in quick_markers):
        return "local"
    if any(marker in lowered for marker in change_markers):
        return "persistent"
    return "persistent"


def normalize_intent(payload: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(payload)
    source = str(values.get("source", "direct-request"))
    if source not in INTAKE_SOURCES:
        raise ValueError(f"intake source must be one of: {', '.join(sorted(INTAKE_SOURCES))}")
    goal = _text(
        values.get("goal", values.get("text", values.get("request"))),
        "goal",
        required=True,
    )
    expected_outcome = _text(values.get("expected_outcome"), "expected_outcome")
    scope = _strings(values.get("scope"), "scope")
    constraints = _strings(values.get("constraints"), "constraints")
    acceptance_criteria = _strings(
        values.get("acceptance_criteria"), "acceptance_criteria"
    )
    change = str(values.get("change") or _infer_change(goal, source))
    if change not in CHANGE_VALUES:
        raise ValueError(f"intake change must be one of: {', '.join(sorted(CHANGE_VALUES))}")
    risk = str(values.get("risk", "low"))
    if risk not in RISK_VALUES:
        raise ValueError(f"intake risk must be one of: {', '.join(sorted(RISK_VALUES))}")
    ambiguous = bool(values.get("ambiguous", False))
    if change == "persistent" and not expected_outcome:
        ambiguous = True
    return {
        "source": source,
        "goal": goal,
        "expected_outcome": expected_outcome,
        "scope": scope,
        "constraints": constraints,
        "acceptance_criteria": acceptance_criteria,
        "change": change,
        "risk": risk,
        "ambiguous": ambiguous,
        "multi_party": bool(values.get("multi_party", False)),
        "remote": bool(values.get("remote", False)),
        "sensitive": bool(values.get("sensitive", False)),
    }


def intake(payload: Mapping[str, Any]) -> dict[str, Any]:
    intent = normalize_intent(payload)
    decision: RouteDecision = classify_work(
        RouteRequest(
            change=intent["change"],
            risk=intent["risk"],
            ambiguous=intent["ambiguous"],
            multi_party=intent["multi_party"],
            remote=intent["remote"],
            sensitive=intent["sensitive"],
        )
    )
    next_action = {
        WorkRoute.QUERY: "answer directly without creating a Unit",
        WorkRoute.QUICK_CHANGE: "confirm intent, make the minimal local change, and verify it",
        WorkRoute.UNIT: "run Inception questions and create a Unit before implementation",
    }[decision.route]
    return {
        "intent": intent,
        "route": decision.as_dict(),
        "next_action": next_action,
    }
