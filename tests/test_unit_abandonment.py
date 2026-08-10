from __future__ import annotations

import json
from pathlib import Path

import pytest

from isekai.workflow import (
    ALLOWED_TRANSITIONS,
    LIFECYCLE_STATUSES,
    record_decision,
    transition_unit,
    verify_unit,
)
from isekai.workflow.active_binding import (
    active_unit_binding,
    bind_active_unit,
    complete_active_unit,
)
from isekai.workflow.errors import LifecycleError
from isekai.catalog.ai_dlc.unit.amendments import record_unit_amendment
from isekai.catalog.ai_dlc.unit.decisions import TERMINAL_STATUSES
from isekai.catalog.ai_dlc.unit.managed_execution import (
    execute_managed_edit,
    write_unit_artifacts,
)

from test_decision_lifecycle import make_unit, start_construction


def _unit_value(unit: Path) -> dict[str, object]:
    return json.loads((unit / "unit.json").read_text(encoding="utf-8"))


def abandon_decision(unit: Path) -> None:
    record_decision(
        unit,
        gate="abandonment",
        outcome="approved",
        summary="이 Unit의 작업을 중단하고 폐기한다.",
        rationale=["우선순위 변경으로 이 작업을 계속하지 않기로 결정했다."],
        alternatives=[
            {
                "option": "Unit을 미완료 상태로 유지한다.",
                "reason": "좀비 Unit이 active 경계를 계속 점유하므로 기각했다.",
            }
        ],
        tradeoffs=["지금까지의 산출물은 보존되지만 진행은 종료된다."],
        risks=["폐기 후에는 같은 Unit에서 작업을 재개할 수 없다."],
        references=["checkpoint.json"],
        decided_by="human-reviewer",
    )


def test_every_open_status_can_reach_abandoned_and_terminals_cannot() -> None:
    for status in LIFECYCLE_STATUSES:
        if status in TERMINAL_STATUSES:
            assert ALLOWED_TRANSITIONS[status] == ()
        else:
            assert "abandoned" in ALLOWED_TRANSITIONS[status]


def test_transition_to_abandoned_requires_an_approved_abandonment_decision(
    tmp_path: Path,
) -> None:
    unit = make_unit(tmp_path)
    with pytest.raises(
        LifecycleError,
        match="requires an approved abandonment Decision",
    ):
        transition_unit(unit, "abandoned")
    assert _unit_value(unit)["status"] == "proposed"


def test_abandonment_closes_the_unit_against_further_work(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    assert verify_unit(unit)["human_gate"]["next_transition"] == "validation"

    abandon_decision(unit)
    result = transition_unit(unit, "abandoned")
    assert result["to"] == "abandoned"
    assert result["required_gate"] == "abandonment"

    unit_value = _unit_value(unit)
    assert unit_value["status"] == "abandoned"
    assert unit_value["phase"] == "closed"

    with pytest.raises(LifecycleError, match="invalid lifecycle transition"):
        transition_unit(unit, "validation")
    with pytest.raises(LifecycleError, match="cannot be amended"):
        record_unit_amendment(
            unit,
            request="폐기된 Unit의 계획을 수정한다.",
            reason="폐기 이후의 수정이 차단되는지 검증한다.",
            affected_artifacts=["plan.md"],
            requested_by="human-reviewer",
        )
    with pytest.raises(LifecycleError, match="cannot execute managed edits"):
        execute_managed_edit(
            unit,
            changes=[
                {
                    "target": "src/example.py",
                    "expected_digest": "absent",
                    "content": "print('blocked')\n",
                }
            ],
        )
    with pytest.raises(LifecycleError, match="cannot change Unit artifacts"):
        write_unit_artifacts(
            unit,
            artifacts=[
                {
                    "target": "plan.md",
                    "expected_digest": "absent",
                    "content": "# 계획\n",
                }
            ],
        )


def test_abandonment_releases_the_active_unit_binding(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    project = unit.parent.parent / "project.json"
    start_construction(unit)

    binding = bind_active_unit(project, unit)
    assert binding["active"] is True

    abandon_decision(unit)
    transition_unit(unit, "abandoned")

    assert active_unit_binding(project)["active"] is False
    completed = complete_active_unit(project, unit)
    assert completed["active"] is False
    state = json.loads(
        Path(completed["state_path"]).read_text(encoding="utf-8")
    )
    assert state["active_unit"] is None
    assert state["events"][-1]["action"] == "abandoned"

    replay = bind_active_unit(project, unit)
    assert replay["active"] is False
