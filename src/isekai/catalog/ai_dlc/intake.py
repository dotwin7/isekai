from __future__ import annotations

import re
from typing import Any, Mapping

from isekai.support.errors import WorkflowError
from isekai.catalog.ai_dlc.routing import RouteDecision, RouteRequest, WorkRoute, classify_work


INTAKE_SOURCES = {"host-goal", "direct-request"}
CHANGE_VALUES = {"none", "local", "persistent"}
RISK_VALUES = {"low", "high"}


SENSITIVE_MARKERS = (
    "credential",
    "credentials",
    "secret",
    "secrets",
    "password",
    "passwords",
    "api key",
    "api keys",
    "access token",
    "access tokens",
    "private key",
    "private keys",
    "customer data",
    "personal data",
    "personally identifiable information",
    "pii",
    "자격증명",
    "비밀정보",
    "민감정보",
    "비밀번호",
    "암호",
    "api 키",
    "액세스 토큰",
    "개인 키",
    "고객 데이터",
    "개인정보",
)
REMOTE_MARKERS = (
    "production environment",
    "production server",
    "production database",
    "prod environment",
    "remote environment",
    "remote server",
    "cloud account",
    "kubernetes cluster",
    "운영 환경",
    "운영 서버",
    "운영 데이터베이스",
    "프로덕션 환경",
    "프로덕션 서버",
    "프로덕션 데이터베이스",
    "원격 환경",
    "원격 서버",
    "클라우드 계정",
    "쿠버네티스 클러스터",
)
HIGH_RISK_MARKERS = (
    "drop database",
    "delete production data",
    "wipe production data",
    "rotate credentials",
    "revoke credentials",
    "incident response",
    "exploit production",
    "penetration test",
    "red team",
    "운영 데이터 삭제",
    "프로덕션 데이터 삭제",
    "데이터베이스 삭제",
    "자격증명 회전",
    "자격증명 폐기",
    "사고 대응",
    "모의해킹",
    "레드팀",
)
MULTI_PARTY_MARKERS = (
    "multiple stakeholders",
    "cross-team approval",
    "security approval",
    "여러 이해관계자",
    "여러 팀 승인",
    "부서간 승인",
    "부서 간 승인",
    "보안 승인",
)


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
        },
        "question_policy": "ask-only-when-answer-materially-changes-plan",
        "human_gate": "approve-level-1-plan-and-consequential-decisions",
    }


def _text(value: Any, field: str, *, required: bool = False) -> str:
    if value is None:
        if required:
            raise WorkflowError(f"intake field must be non-empty: {field}")
        return ""
    if not isinstance(value, str):
        raise WorkflowError(f"intake field must be text: {field}")
    if not value.strip():
        if required:
            raise WorkflowError(f"intake field must be non-empty: {field}")
        return ""
    return value.strip()


def _strings(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise WorkflowError(f"intake field must be a list of non-empty strings: {field}")
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
        if marker.isascii() and re.search(r"[A-Za-z0-9_]", marker):
            # ASCII word boundaries should still match Korean particles joined
            # directly to a term, as in ``credentials를``. ``\w`` includes
            # Unicode letters and incorrectly hides that safety signal.
            if re.search(
                rf"(?<![A-Za-z0-9_]){re.escape(marker)}(?![A-Za-z0-9_])",
                text,
            ):
                return True
        elif marker in text:
            return True
    return False


def _boolean(value: Any, field: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise WorkflowError(f"intake field must be boolean: {field}")
    return value


def _infer_context_signals(text: str) -> dict[str, bool]:
    """Infer conservative safety signals from the request text.

    Runtime Adapters still classify the full conversation and must pass explicit
    flags. These markers are a fail-safe for direct Core callers and for an
    Adapter that accidentally omits an obvious signal; they are not intended to
    replace host-agent judgment.
    """

    lowered = text.lower()
    return {
        "high_risk": _matches(lowered, HIGH_RISK_MARKERS),
        "remote": _matches(lowered, REMOTE_MARKERS),
        "sensitive": _matches(lowered, SENSITIVE_MARKERS),
        "multi_party": _matches(lowered, MULTI_PARTY_MARKERS),
    }


def _is_bounded_quick_change(text: str) -> bool:
    if _matches(
        text,
        (
            "all files",
            "every file",
            "multiple files",
            "entire repository",
            "whole repository",
            "whole repo",
            "across the repository",
            "모든 파일",
            "여러 파일",
            "저장소 전체",
            "프로젝트 전체",
        ),
    ):
        return False
    if re.search(
        r"\b(?:add|implement|build)\b.{0,40}\b(?:feature|support|option|capability)\b",
        text,
    ) or re.search(
        r"(?:기능|지원|옵션).{0,20}(?:추가|구현|개발)|"
        r"(?:추가|구현|개발).{0,20}(?:기능|지원|옵션)",
        text,
    ):
        return False
    quick_markers = (
        "오타",
        "typo",
        "문구",
        "format",
        "whitespace",
        "동작을 바꾸지 않는 정리",
        "동작 변경 없는 정리",
        "behavior-preserving cleanup",
        "without changing behavior",
        "no behavior change",
    )
    if _matches(text, quick_markers):
        return True

    single_file = _matches(text, ("단일 파일", "한 파일", "single file", "one file"))
    obvious = _matches(text, ("명백", "obvious", "straightforward", "trivial"))
    bug = _matches(text, ("버그", "bug", "null check", "널 체크"))
    return single_file and obvious and bug


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
        return "local" if _is_bounded_quick_change(lowered) else "persistent"
    if _matches(lowered, question_markers):
        return "none"
    if _is_bounded_quick_change(lowered):
        return "local"
    if _matches(lowered, change_markers):
        return "persistent"
    # Unrecognized phrasing falls through to the safest route: a Unit asks for
    # explicit intent and human approval rather than acting on a guess.
    return "persistent"


def normalize_intent(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise WorkflowError("intake payload must be an object")
    values = dict(payload)
    source = values.get("source", "direct-request")
    if not isinstance(source, str) or source not in INTAKE_SOURCES:
        raise WorkflowError(f"intake source must be one of: {', '.join(sorted(INTAKE_SOURCES))}")
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
    inferred_change = _infer_change(goal, source)
    raw_change = values.get("change")
    change = inferred_change if raw_change is None else raw_change
    if not isinstance(change, str) or change not in CHANGE_VALUES:
        raise WorkflowError(f"intake change must be one of: {', '.join(sorted(CHANGE_VALUES))}")
    # Structured callers often keep the short action in ``goal`` and place the
    # consequential system or data in the remaining intent fields.  Scan the
    # complete normalized context so a direct Core call cannot hide an obvious
    # safety signal merely by moving it from prose into ``scope``.
    signal_context = "\n".join(
        (
            goal,
            expected_outcome,
            *scope,
            *constraints,
            *acceptance_criteria,
        )
    )
    signals = _infer_context_signals(signal_context)
    risk = values.get("risk", "low")
    if not isinstance(risk, str) or risk not in RISK_VALUES:
        raise WorkflowError(f"intake risk must be one of: {', '.join(sorted(RISK_VALUES))}")
    if signals["high_risk"]:
        risk = "high"
    ambiguous = _boolean(values.get("ambiguous"), "ambiguous")
    if change == "persistent" and not expected_outcome:
        ambiguous = True
    multi_party = _boolean(values.get("multi_party"), "multi_party") or signals[
        "multi_party"
    ]
    remote = _boolean(values.get("remote"), "remote") or signals["remote"]
    sensitive = _boolean(values.get("sensitive"), "sensitive") or signals[
        "sensitive"
    ]
    prior_classification = values.get("classification")
    prior_change_source = (
        prior_classification.get("change_source")
        if isinstance(prior_classification, dict)
        else None
    )
    change_source = (
        prior_change_source
        if isinstance(prior_change_source, str)
        and prior_change_source in {"declared", "inferred"}
        else ("declared" if raw_change is not None else "inferred")
    )
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
        "multi_party": multi_party,
        "remote": remote,
        "sensitive": sensitive,
        "classification": {
            "change_source": change_source,
            "inferred_signals": sorted(
                name for name, detected in signals.items() if detected
            ),
        },
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
