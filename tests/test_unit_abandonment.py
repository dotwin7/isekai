from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

import pytest

import isekai.workflow.active_binding as active_binding_module
from isekai.runtime_contract import dispatch
from isekai.workflow import (
    ALLOWED_TRANSITIONS,
    LIFECYCLE_STATUSES,
    record_decision,
    transition_unit,
    verify_unit,
)
from isekai.workflow.active_binding import (
    active_unit_action_guard,
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
from test_core_workflow import make_project


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
    with active_unit_action_guard(project, unit, action="transition") as complete:
        transition_unit(unit, "abandoned")
        completed = complete()

    assert active_unit_binding(project)["active"] is False
    assert completed["active"] is False
    state = json.loads(
        Path(completed["state_path"]).read_text(encoding="utf-8")
    )
    assert state["active_unit"] is None
    assert state["events"][-1]["action"] == "abandoned"

    # Recovery remains idempotent after the terminal event was committed inside
    # the same binding lock as the transition action.
    completed = complete_active_unit(project, unit)
    state = json.loads(Path(completed["state_path"]).read_text(encoding="utf-8"))
    assert [event["action"] for event in state["events"]].count("abandoned") == 1

    replay = bind_active_unit(project, unit)
    assert replay["active"] is False


def test_terminal_transition_closes_binding_before_another_unit_can_bind(
    tmp_path: Path,
) -> None:
    unit = make_unit(tmp_path)
    project = unit.parent.parent / "project.json"
    start_construction(unit)
    bind_active_unit(project, unit)
    abandon_decision(unit)

    def create_next_unit() -> dict[str, object]:
        return dispatch(
            "unit-init",
            {
                "project": str(project),
                "title": "폐기 직후의 새 Unit",
                "owner": "human-owner",
            },
        )["result"]

    with ThreadPoolExecutor(max_workers=1) as executor:
        with active_unit_action_guard(project, unit, action="transition") as complete:
            transition_unit(unit, "abandoned")
            future = executor.submit(create_next_unit)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
            complete()
        created = future.result(timeout=2)

    state = json.loads(
        (project.parent / ".isekai-runtime/active-unit.json").read_text(
            encoding="utf-8"
        )
    )
    assert [event["action"] for event in state["events"]] == [
        "bind",
        "abandoned",
        "bind",
    ]
    created_unit = json.loads(
        Path(str(created["created"])).joinpath("unit.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["active_unit"]["unit_id"] == created_unit["id"]


def test_next_unit_reconciles_a_terminal_event_after_binding_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = make_unit(tmp_path)
    project = unit.parent.parent / "project.json"
    start_construction(unit)
    bind_active_unit(project, unit)
    abandon_decision(unit)
    original_write_event = active_binding_module._write_event
    failed = False

    def fail_first_terminal_event(
        project_manifest: Path,
        binding: dict[str, object],
        event: dict[str, object],
        active_unit: dict[str, str] | None,
    ) -> dict[str, object]:
        nonlocal failed
        if event.get("action") == "abandoned" and not failed:
            failed = True
            raise OSError("forced terminal binding write failure")
        return original_write_event(project_manifest, binding, event, active_unit)

    monkeypatch.setattr(active_binding_module, "_write_event", fail_first_terminal_event)

    with pytest.raises(OSError, match="forced terminal binding write failure"):
        dispatch("transition", {"unit": str(unit), "to": "abandoned"})

    assert _unit_value(unit)["status"] == "abandoned"
    created = dispatch(
        "unit-init",
        {
            "project": str(project),
            "title": "복구 뒤 새 Unit",
            "owner": "human-owner",
        },
    )["result"]
    state = json.loads(
        (project.parent / ".isekai-runtime/active-unit.json").read_text(
            encoding="utf-8"
        )
    )

    assert [event["action"] for event in state["events"]] == [
        "bind",
        "abandoned",
        "bind",
    ]
    assert Path(str(created["created"])).is_dir()


def test_unit_initialization_rolls_back_when_binding_commit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    units_root = project.parent / "units"

    def fail_binding_commit(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise OSError("forced Unit binding commit failure")

    monkeypatch.setattr(active_binding_module, "_write_event", fail_binding_commit)

    with pytest.raises(OSError, match="forced Unit binding commit failure"):
        dispatch(
            "unit-init",
            {
                "project": str(project),
                "title": "결박 실패 Unit",
                "owner": "human-owner",
            },
        )

    assert units_root.is_dir()
    assert list(units_root.iterdir()) == []
