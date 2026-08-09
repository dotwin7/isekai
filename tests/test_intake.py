from __future__ import annotations

import json
from pathlib import Path

import pytest

from isekai.intake import intake, normalize_intent
from isekai.workflow import initialize_unit
from isekai.workflow.errors import WorkflowError

from test_core_workflow import make_project


def test_direct_question_routes_to_query_without_unit_artifacts() -> None:
    result = intake({"source": "direct-request", "goal": "Entity가 뭐야?"})

    assert result["intent"]["change"] == "none"
    assert result["route"]["route"] == "query"
    assert result["workflow"] == {
        "version": "1.0.0",
        "driver": "direct-response",
        "artifact_mode": "none",
        "plan": {"required": False},
        "human_gate": "none",
        "steps": ["inspect-as-needed", "answer"],
    }
    assert "without creating a Unit" in result["next_action"]


@pytest.mark.parametrize(
    "goal",
    [
        "프로젝트 구조를 먼저 파악해봐",
        "현재 구현 성숙도를 분석해줘",
        "이 프로젝트 리뷰해줘",
        "Review the current architecture",
    ],
)
def test_read_only_analysis_routes_to_query(goal: str) -> None:
    result = intake({"source": "direct-request", "goal": goal})

    assert result["intent"]["change"] == "none"
    assert result["route"]["route"] == "query"


def test_analysis_feature_implementation_still_routes_to_unit() -> None:
    result = intake(
        {
            "goal": "분석 기능을 구현해줘",
            "expected_outcome": "분석 결과를 저장한다",
        }
    )

    assert result["intent"]["change"] == "persistent"
    assert result["route"]["route"] == "unit"


@pytest.mark.parametrize(
    "goal",
    [
        "Fix the auth bug?",
        "Could you fix the auth bug?",
        "Review and fix this code",
        "로그인 버그 수정 가능해?",
    ],
)
def test_change_requests_with_question_or_review_language_route_to_unit(
    goal: str,
) -> None:
    result = intake({"source": "direct-request", "goal": goal})

    assert result["intent"]["change"] == "persistent"
    assert result["route"]["route"] == "unit"


def test_how_to_fix_question_remains_a_query() -> None:
    result = intake({"goal": "How do I fix the auth bug?"})

    assert result["intent"]["change"] == "none"
    assert result["route"]["route"] == "query"


def test_direct_small_text_change_routes_to_quick_change() -> None:
    result = intake({"goal": "README 오타를 고쳐줘"})

    assert result["intent"]["change"] == "local"
    assert result["route"]["route"] == "quick-change"
    assert result["workflow"]["driver"] == "bounded-change"
    assert result["workflow"]["artifact_mode"] == "conversation"
    assert result["workflow"]["plan"] == {
        "required": True,
        "level": "compact",
        "approval": "covered-by-explicit-request",
        "required_sections": ["scope", "change", "verification"],
    }
    assert result["workflow"]["human_gate"] == "only-if-scope-or-risk-expands"


@pytest.mark.parametrize(
    "goal",
    [
        "src/example.py 한 파일의 명백한 null check 버그를 고쳐줘",
        "Fix an obvious bug in a single file",
        "Do a behavior-preserving cleanup without changing behavior",
    ],
)
def test_explicit_bounded_single_file_change_routes_to_quick_change(
    goal: str,
) -> None:
    result = intake({"goal": goal})

    assert result["intent"]["change"] == "local"
    assert result["route"]["route"] == "quick-change"


def test_vague_bug_fix_remains_a_unit() -> None:
    result = intake({"goal": "Fix the authentication bug"})

    assert result["intent"]["change"] == "persistent"
    assert result["route"]["route"] == "unit"


@pytest.mark.parametrize(
    "goal",
    [
        "Format all files in the repository",
        "Add a format option to the export feature",
        "프로젝트 전체 파일의 문구를 수정해줘",
    ],
)
def test_broad_or_feature_formatting_requests_remain_units(goal: str) -> None:
    result = intake({"goal": goal})

    assert result["intent"]["change"] == "persistent"
    assert result["route"]["route"] == "unit"


@pytest.mark.parametrize(
    ("goal", "field", "signal"),
    [
        (
            "production credentials를 읽고 요약해줘",
            "sensitive",
            "sensitive",
        ),
        (
            "운영 서버의 현재 상태를 조회해줘",
            "remote",
            "remote",
        ),
        (
            "여러 팀 승인이 필요한 변경을 검토해줘",
            "multi_party",
            "multi_party",
        ),
    ],
)
def test_obvious_context_signals_are_inferred_and_escalated(
    goal: str,
    field: str,
    signal: str,
) -> None:
    result = intake({"goal": goal})

    assert result["intent"][field] is True
    assert signal in result["intent"]["classification"]["inferred_signals"]
    assert result["route"]["route"] == "unit"
    assert result["workflow"]["plan"]["suggested_depth"] == "deep"


def test_high_risk_text_cannot_be_downgraded_by_default_or_explicit_low_risk() -> None:
    result = intake(
        {
            "goal": "운영 데이터 삭제 절차를 검토해줘",
            "risk": "low",
            "sensitive": False,
            "remote": False,
        }
    )

    assert result["intent"]["risk"] == "high"
    assert "high_risk" in result["intent"]["classification"]["inferred_signals"]
    assert result["route"]["route"] == "unit"


def test_explicit_false_cannot_downgrade_inferred_sensitive_context() -> None:
    result = intake(
        {
            "goal": "production credentials를 읽고 요약해줘",
            "change": "none",
            "sensitive": False,
        }
    )

    assert result["intent"]["sensitive"] is True
    assert result["route"]["route"] == "unit"


@pytest.mark.parametrize(
    ("field", "value", "signal"),
    [
        ("scope", ["production database"], "remote"),
        ("constraints", ["credentials must remain read-only"], "sensitive"),
        ("expected_outcome", "운영 데이터 삭제 절차를 확인한다", "high_risk"),
        ("acceptance_criteria", ["여러 팀 승인을 기록한다"], "multi_party"),
    ],
)
def test_structured_intent_fields_contribute_safety_signals(
    field: str,
    value: object,
    signal: str,
) -> None:
    result = intake({"goal": "현재 상태를 요약해줘", field: value})

    assert signal in result["intent"]["classification"]["inferred_signals"]
    assert result["route"]["route"] == "unit"
    assert result["workflow"]["plan"]["suggested_depth"] == "deep"


@pytest.mark.parametrize("field", ["ambiguous", "multi_party", "remote", "sensitive"])
def test_intake_rejects_non_boolean_context_flags(field: str) -> None:
    with pytest.raises(WorkflowError, match=field):
        normalize_intent({"goal": "Inspect the project", field: "false"})


def test_host_goal_routes_to_unit_and_preserves_outcome_and_scope() -> None:
    result = intake(
        {
            "source": "host-goal",
            "goal": "결제 서비스에 이벤트 분류 기능을 추가한다",
            "expected_outcome": "분류 결과와 원본 참조를 저장한다",
            "scope": ["src/payment_events/**", "tests/**"],
            "constraints": ["production 변경 금지"],
            "acceptance_criteria": ["분류 테스트 통과"],
            "risk": "low",
        }
    )

    assert result["intent"]["change"] == "persistent"
    assert result["route"]["route"] == "unit"
    assert result["intent"]["scope"] == ["src/payment_events/**", "tests/**"]
    assert result["intent"]["acceptance_criteria"] == ["분류 테스트 통과"]
    workflow = result["workflow"]
    assert workflow["driver"] == "adaptive-unit"
    assert workflow["artifact_mode"] == "unit"
    assert workflow["plan"]["level"] == "level-1"
    assert workflow["plan"]["approval"] == "explicit-before-unit-write"
    assert workflow["plan"]["suggested_depth"] == "standard"
    assert workflow["plan"]["stage_decisions"]["release"] == (
        "agent-proposes-apply-or-skip"
    )
    assert workflow["question_policy"] == (
        "ask-only-when-answer-materially-changes-plan"
    )
    assert result["next_action"] == (
        "inspect read-only and obtain approval for a Level-1 plan before Unit writes"
    )


@pytest.mark.parametrize(
    "risk_flags",
    [
        {"risk": "high"},
        {"remote": True},
        {"sensitive": True},
        {"multi_party": True},
    ],
)
def test_unit_workflow_suggests_deep_planning_for_consequential_work(
    risk_flags: dict[str, object],
) -> None:
    result = intake(
        {
            "goal": "인증 흐름을 변경해줘",
            "expected_outcome": "인증 계약을 갱신한다",
            **risk_flags,
        }
    )

    assert result["route"]["route"] == "unit"
    assert result["workflow"]["plan"]["suggested_depth"] == "deep"


def test_persistent_direct_request_without_outcome_is_marked_ambiguous() -> None:
    result = intake({"goal": "결제 서비스를 리팩터링하자", "change": "persistent"})

    assert result["route"]["route"] == "unit"
    assert "ambiguous acceptance criteria" in result["route"]["reasons"]
    assert result["workflow"]["plan"]["suggested_depth"] == "deep"


@pytest.mark.parametrize(
    "risk_flags",
    [
        {"risk": "high"},
        {"remote": True},
        {"sensitive": True},
    ],
)
def test_read_only_request_with_risk_signal_is_escalated_to_unit(
    risk_flags: dict[str, object],
) -> None:
    result = intake(
        {
            "goal": "현재 상태를 조회해줘",
            "change": "none",
            **risk_flags,
        }
    )

    assert result["route"]["route"] == "unit"


def test_intake_rejects_missing_goal_and_unknown_source() -> None:
    with pytest.raises(WorkflowError, match="goal"):
        normalize_intent({"source": "direct-request"})
    with pytest.raises(WorkflowError, match="source"):
        normalize_intent({"source": "unknown", "goal": "build"})


def test_unit_init_persists_normalized_intent_metadata(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    normalized = normalize_intent(
        {
            "source": "host-goal",
            "goal": "Add event classifier",
            "expected_outcome": "Classify events",
            "scope": ["src/events/**"],
            "constraints": ["no production changes"],
            "acceptance_criteria": ["tests pass"],
        }
    )

    unit = initialize_unit(
        project,
        "Add event classifier",
        project.parent / "units",
        intent=normalized,
    )

    unit_json = json.loads((unit / "unit.json").read_text(encoding="utf-8"))
    intent_markdown = (unit / "intent.md").read_text(encoding="utf-8")
    assert unit_json["intent_source"] == "host-goal"
    assert unit_json["goal"] == "Add event classifier"
    assert unit_json["work_scope"] == ["src/events/**"]
    assert unit_json["intake"] == {
        "change": "persistent",
        "risk": "low",
        "ambiguous": False,
        "multi_party": False,
        "remote": False,
        "sensitive": False,
        "classification": {
            "change_source": "inferred",
            "inferred_signals": [],
        },
    }
    assert "Add event classifier" in intent_markdown
    assert "Classify events" in intent_markdown


def test_unit_init_rejects_non_normalized_intent_fields(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    with pytest.raises(WorkflowError, match="source must be one of"):
        initialize_unit(
            project,
            "Invalid source",
            intent={"source": "not-a-source", "goal": "Build it"},
        )
    with pytest.raises(WorkflowError, match="scope"):
        initialize_unit(
            project,
            "Invalid scope",
            intent={
                "source": "direct-request",
                "goal": "Build it",
                "scope": "src/**",
            },
        )
