from __future__ import annotations

import json
from pathlib import Path

import pytest

from isekai.intake import intake, normalize_intent
from isekai.workflow import initialize_unit

from test_core_workflow import make_project


def test_direct_question_routes_to_query_without_workflow() -> None:
    result = intake({"source": "direct-request", "goal": "Entity가 뭐야?"})

    assert result["intent"]["change"] == "none"
    assert result["route"]["route"] == "query"
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


def test_direct_small_text_change_routes_to_quick_change() -> None:
    result = intake({"goal": "README 오타를 고쳐줘"})

    assert result["intent"]["change"] == "local"
    assert result["route"]["route"] == "quick-change"


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


def test_persistent_direct_request_without_outcome_is_marked_ambiguous() -> None:
    result = intake({"goal": "결제 서비스를 리팩터링하자", "change": "persistent"})

    assert result["route"]["route"] == "unit"
    assert "ambiguous acceptance criteria" in result["route"]["reasons"]


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
    with pytest.raises(ValueError, match="goal"):
        normalize_intent({"source": "direct-request"})
    with pytest.raises(ValueError, match="source"):
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
    assert "Add event classifier" in intent_markdown
    assert "Classify events" in intent_markdown


def test_unit_init_rejects_non_normalized_intent_fields(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    with pytest.raises(ValueError, match="source must be one of"):
        initialize_unit(
            project,
            "Invalid source",
            intent={"source": "not-a-source", "goal": "Build it"},
        )
    with pytest.raises(ValueError, match="scope"):
        initialize_unit(
            project,
            "Invalid scope",
            intent={
                "source": "direct-request",
                "goal": "Build it",
                "scope": "src/**",
            },
        )
