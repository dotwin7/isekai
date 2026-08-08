from __future__ import annotations

import re
from typing import Any, Mapping

from .routing import RouteDecision, RouteRequest, WorkRoute, classify_work


INTAKE_SOURCES = {"host-goal", "direct-request"}
CHANGE_VALUES = {"none", "local", "persistent"}
RISK_VALUES = {"low", "high"}


def _workflow_directive(
    intent: Mapping[str, Any], decision: RouteDecision
) -> dict[str, Any]:
    """Describe how the host agent should drive the selected route.

    Core classifies and constrains the work, but the host agent remains the
    planner.  This contract keeps that boundary explicit and gives every
    runtime Adapter the same orchestration vocabulary.
    """
    if decision.route is WorkRoute.QUERY:
        return {
            "version": "1.0.0",
            "driver": "direct-response",
            "artifact_mode": "none",
            "plan": {"required": False},
            "human_gate": "none",
            "steps": ["inspect-as-needed", "answer"],
        }
    if decision.route is WorkRoute.QUICK_CHANGE:
        return {
            "version": "1.0.0",
            "driver": "bounded-change",
            "artifact_mode": "conversation",
            "plan": {
                "required": True,
                "level": "compact",
                "approval": "covered-by-explicit-request",
                "required_sections": ["scope", "change", "verification"],
            },
            "human_gate": "only-if-scope-or-risk-expands",
            "steps": ["inspect", "change", "verify", "report"],
        }

    planning_depth = (
        "deep"
        if intent["risk"] == "high"
        or intent["ambiguous"]
        or intent["sensitive"]
        or intent["remote"]
        or intent["multi_party"]
        else "standard"
    )
    return {
        "version": "1.0.0",
        "driver": "adaptive-unit",
        "artifact_mode": "unit",
        "plan": {
            "required": True,
            "level": "level-1",
            "approval": "explicit-before-unit-write",
            "suggested_depth": planning_depth,
            "required_sections": [
                "goal",
                "expected_outcome",
                "scope",
                "non_goals",
                "acceptance_criteria",
                "risks",
                "stages",
                "verification",
            ],
            "stage_decisions": {
                "inception": "required",
                "construction": "required",
                "validation": "required",
                "release": "agent-proposes-apply-or-skip",
                "operations": "agent-proposes-apply-or-skip",
                "learn": "required",
            },
            "depth_options": ["light", "standard", "deep"],
        },
        "question_policy": "ask-only-when-answer-materially-changes-plan",
        "human_gate": "approve-level-1-plan-and-consequential-decisions",
        "steps": [
            "inspect-read-only",
            "propose-level-1-plan",
            "resolve-material-questions",
            "obtain-plan-approval",
            "initialize-unit",
            "execute-approved-stages",
            "verify-and-learn",
        ],
    }


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


def _matches(text: str, markers: tuple[str, ...]) -> bool:
    """Match markers as whole words where the script has word boundaries.

    Substring matching made ``deploy`` inside a filename look like a deployment
    request. Korean has no word boundaries, so those markers stay substrings.
    """
    for marker in markers:
        marker = marker.strip()
        if not marker:
            continue
        if marker.isascii() and re.search(r"\w", marker):
            if re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", text):
                return True
        elif marker in text:
            return True
    return False


def _infer_change(text: str, source: str) -> str:
    if source == "host-goal":
        return "persistent"
    lowered = text.lower()
    question_markers = (
        "?",
        "무엇",
        "뭐가",
        "왜",
        "어떻게",
        "설명",
        "파악",
        "조회",
        "요약",
        "분석",
        "검토",
        "리뷰",
        "what ",
        "why ",
        "how ",
        "inspect",
        "summarize",
        "analyze",
        "review ",
    )
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
    requested_change_markers = (
        "개발해",
        "개발하",
        "추가해",
        "추가하",
        "수정해",
        "수정하",
        "변경해",
        "변경하",
        "구현해",
        "구현하",
        "만들어",
        "만들자",
        "리팩터해",
        "리팩터하",
        "배포해",
        "배포하",
        "삭제해",
        "삭제하",
        "please fix",
        "please add",
        "please change",
        "please implement",
        "can you fix",
        "can you add",
        "can you implement",
    )
    quick_markers = ("오타", "typo", "문구", "format", "whitespace")
    english_actions = r"fix|add|change|implement|build|refactor|deploy|delete"
    explicit_change_request = _matches(lowered, requested_change_markers) or any(
        re.search(pattern, lowered)
        for pattern in (
            rf"^\s*(?:please\s+)?(?:{english_actions})\b",
            rf"\b(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:{english_actions})\b",
            rf"\band\s+(?:then\s+)?(?:{english_actions})\b",
            r"(?:개발|추가|수정|변경|구현|배포|삭제)\s*(?:가능|부탁)",
        )
    )
    if explicit_change_request:
        return "local" if _matches(lowered, quick_markers) else "persistent"
    if _matches(lowered, question_markers):
        return "none"
    if _matches(lowered, quick_markers):
        return "local"
    if _matches(lowered, change_markers):
        return "persistent"
    # Unrecognized phrasing falls through to the safest route: a Unit asks for
    # explicit intent and human approval rather than acting on a guess.
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
        WorkRoute.QUICK_CHANGE: (
            "state a compact plan, make the minimal local change, and verify it"
        ),
        WorkRoute.UNIT: (
            "inspect read-only and obtain approval for a Level-1 plan before Unit writes"
        ),
    }[decision.route]
    return {
        "intent": intent,
        "route": decision.as_dict(),
        "workflow": _workflow_directive(intent, decision),
        "next_action": next_action,
    }
