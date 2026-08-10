from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from isekai.runtime_contract import dispatch
from isekai.workflow import initialize_unit
from isekai.workflow.errors import IntegrityError, LifecycleError

from test_core_workflow import make_project


def _runtime_unit(project: Path, title: str = "활성 Unit") -> Path:
    created = dispatch(
        "unit-init",
        {
            "project": str(project),
            "title": title,
            "owner": "human-owner",
        },
    )
    return Path(created["result"]["created"])


def test_core_blocks_new_intake_and_unit_creation_while_unit_is_active(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    unit = _runtime_unit(project)

    blocked = dispatch(
        "intake",
        {
            "project": str(project),
            "source": "direct-request",
            "goal": "기존 작업에 정렬 조건을 추가한다.",
            "change": "persistent",
        },
    )["result"]

    assert blocked["blocked"] is True
    assert blocked["reason_code"] == "active-unit-amendment-required"
    assert blocked["active_unit"]["path"] == str(unit)
    assert blocked["workflow"] == {
        "driver": "active-unit-continuation",
        "required_action": "amend",
        "new_route_allowed": False,
    }
    with pytest.raises(LifecycleError, match="unfinished active Unit"):
        _runtime_unit(project, "우회하려는 새 Unit")


def test_core_blocks_actions_against_a_sibling_of_the_active_unit(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    active = _runtime_unit(project)
    sibling = initialize_unit(project, "형제 Unit", project.parent / "units")

    with pytest.raises(LifecycleError, match="outside the unfinished active Unit"):
        dispatch(
            "checkpoint",
            {
                "unit": str(sibling),
                "completed": [],
                "pending": ["우회 변경"],
                "blocked_by": [],
                "next_action": "형제 Unit을 변경한다.",
            },
        )

    checkpoint = dispatch(
        "checkpoint",
        {
            "unit": str(active),
            "completed": ["현재 Unit 경계 확인"],
            "pending": ["현재 Unit 계속 진행"],
            "blocked_by": [],
            "next_action": "현재 Unit을 계속한다.",
        },
    )["result"]
    assert checkpoint["checkpoint"]["unit_id"]


def test_explicit_detach_preserves_unit_and_allows_a_new_route(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = _runtime_unit(project)
    checkpoint_before = (unit / "checkpoint.json").read_bytes()

    detached = dispatch(
        "active-unit-detach",
        {
            "project": str(project),
            "unit": str(unit),
            "requested_by": "human-owner",
            "reason": "사용자가 현재 Unit을 보존하고 별도 작업으로 전환했다.",
        },
    )["result"]

    assert detached["active"] is False
    assert (unit / "checkpoint.json").read_bytes() == checkpoint_before
    state = json.loads(Path(detached["state_path"]).read_text(encoding="utf-8"))
    assert state["events"][-1]["attestation"]["reported_actor"] == "human-owner"
    routed = dispatch(
        "intake",
        {
            "project": str(project),
            "source": "direct-request",
            "goal": "명시적으로 분리한 새 작업을 시작한다.",
            "change": "persistent",
        },
    )["result"]
    assert "blocked" not in routed
    assert routed["route"]["route"] == "unit"


def test_off_and_new_on_do_not_lose_the_unfinished_active_unit(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = _runtime_unit(project)

    assert dispatch("off")["result"]["active_unit_changed"] is False
    activated = dispatch("on", {"project": str(project)})["result"]

    assert activated["active_unit_binding"]["active"] is True
    assert activated["active_unit_binding"]["unit"]["path"] == str(unit)


def test_final_learned_transition_releases_the_core_binding(tmp_path: Path) -> None:
    from isekai.workflow.session import update_checkpoint
    from isekai.workflow import transition_unit
    from test_decision_lifecycle import (
        approve,
        complete_acceptance,
        make_unit,
        passing_evidence,
        start_construction,
    )

    unit = make_unit(tmp_path)
    project = unit.parent.parent / "project.json"
    dispatch(
        "checkpoint",
        {
            "unit": str(unit),
            "completed": [],
            "pending": ["Unit lifecycle 완료"],
            "blocked_by": [],
            "next_action": "Construction을 시작한다.",
        },
    )
    start_construction(unit)
    approve(unit, "architecture")
    transition_unit(unit, "validation")
    transition_unit(unit, "awaiting-release-decision")
    passing_evidence(unit)
    approve(unit, "release")
    complete_acceptance(unit)
    transition_unit(unit, "releasing")
    transition_unit(unit, "operating")
    passing_evidence(unit)
    approve(unit, "operation")
    update_checkpoint(
        unit,
        completed=["구현, 검증, 운영 승인"],
        pending=[],
        blocked_by=[],
        next_action="Unit을 learned로 전환한다.",
    )

    result = dispatch(
        "transition",
        {"unit": str(unit), "to": "learned"},
    )["result"]

    assert result["to"] == "learned"
    assert result["active_unit_binding"]["active"] is False
    routed = dispatch(
        "intake",
        {
            "project": str(project),
            "source": "direct-request",
            "goal": "완료 뒤 새 기능을 시작한다.",
            "change": "persistent",
        },
    )["result"]
    assert routed["route"]["route"] == "unit"


def test_detach_rejects_stale_checkpoint(tmp_path: Path) -> None:
    from isekai.workflow import (
        propose_execution_envelope,
        record_decision,
        transition_unit,
    )
    from isekai.catalog.ai_dlc.unit.execution import (
        _issue_action_grant as authorize_action,
    )
    from test_core_workflow import materialize_unit_artifacts

    project = make_project(tmp_path)
    unit = _runtime_unit(project)
    materialize_unit_artifacts(unit)
    propose_execution_envelope(
        unit,
        scope=["src/**"],
        stages=[
            {"name": "inception", "depth": "standard", "allowed_actions": ["read"]},
            {
                "name": "construction",
                "depth": "standard",
                "allowed_actions": ["read", "edit"],
            },
            {"name": "validation", "depth": "standard", "allowed_actions": ["read"]},
            {"name": "operations", "depth": "light", "allowed_actions": ["read"]},
        ],
        allowed_actions=["read", "edit"],
        forbidden_actions=["remote", "deploy", "credential-access"],
        max_iterations=2,
        proposed_by="planner-agent",
    )
    transition_unit(unit, "inception")
    transition_unit(unit, "awaiting-inception-decision")
    record_decision(
        unit,
        gate="inception",
        outcome="approved",
        summary="현재 Unit의 실행 범위를 승인한다.",
        rationale=["테스트 경계를 확인한다."],
        alternatives=[],
        tradeoffs=["로컬 변경만 허용한다."],
        risks=["테스트용 Unit이다."],
        references=["execution-envelope.json"],
        decided_by="human-owner",
    )
    transition_unit(unit, "construction")
    assert authorize_action(unit, action="edit", target="src/example.py")["allowed"]

    with pytest.raises(LifecycleError, match="requires a current Checkpoint"):
        dispatch(
            "active-unit-detach",
            {
                "project": str(project),
                "unit": str(unit),
                "requested_by": "human-owner",
                "reason": "별도 작업으로 전환한다.",
            },
        )


def test_binding_state_path_fails_closed_on_symlink(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    external = tmp_path / "external-runtime-state"
    external.mkdir()
    (project.parent / ".isekai-runtime").symlink_to(external, target_is_directory=True)

    with pytest.raises(IntegrityError, match="must be a real directory"):
        _runtime_unit(project)

    assert list(external.iterdir()) == []


def test_binding_event_tampering_fails_closed(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = _runtime_unit(project)
    state_path = project.parent / ".isekai-runtime/active-unit.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["events"][0]["reason"] = "변조된 결박 이유"
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(IntegrityError, match="event 0 digest does not match"):
        dispatch(
            "intake",
            {
                "project": str(project),
                "source": "direct-request",
                "goal": "결박을 우회한다.",
                "change": "persistent",
            },
        )

    assert unit.is_dir()


def test_core_binding_supports_an_explicit_external_unit_output(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    external_root = tmp_path / "external-units"
    created = dispatch(
        "unit-init",
        {
            "project": str(project),
            "title": "외부 경로 Unit",
            "owner": "human-owner",
            "output": str(external_root),
        },
    )["result"]
    unit = Path(created["created"])

    assert created["active_unit_binding"]["unit"]["path"] == str(unit)
    state = json.loads(
        (project.parent / ".isekai-runtime/active-unit.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["active_unit"]["path_base"] == "absolute"
    checkpoint = dispatch(
        "checkpoint",
        {
            "unit": str(unit),
            "completed": ["외부 Unit 결박 확인"],
            "pending": ["계속 진행"],
            "blocked_by": [],
            "next_action": "같은 외부 Unit을 계속한다.",
        },
    )["result"]
    assert checkpoint["checkpoint"]["unit_id"]


def test_concurrent_unit_creation_cannot_open_two_active_units(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    def create(index: int) -> str:
        result = dispatch(
            "unit-init",
            {
                "project": str(project),
                "title": f"동시 Unit {index}",
                "owner": "human-owner",
            },
        )
        return str(result["result"]["created"])

    paths: list[str] = []
    errors: list[Exception] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create, index) for index in range(2)]
        for future in futures:
            try:
                paths.append(future.result())
            except Exception as exc:  # noqa: BLE001 - assert the Core failure below
                errors.append(exc)

    assert len(paths) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], LifecycleError)
    assert "unfinished active Unit" in str(errors[0])
    assert len(list((project.parent / "units").iterdir())) == 1
