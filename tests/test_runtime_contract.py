from __future__ import annotations

from pathlib import Path

import pytest

from isekai.runtime_contract import RuntimeContractError, dispatch
from isekai.workflow.errors import AuthorizationError

from test_core_workflow import make_project, materialize_unit_artifacts
from test_decision_lifecycle import authorize_test
from test_execution_envelope import approve_inception, make_enveloped_unit
from isekai.workflow import initialize_unit


def test_runtime_golden_path_exposes_core_session_contract(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    inception = dispatch("inception", {"project": str(project)})
    assert inception["runtime"] == "isekai-project-runtime"
    assert inception["action"] == "inception"
    assert inception["result"]["inception"]["decision_required"] is True

    unit = initialize_unit(project, "Runtime Golden Path", project.parent / "units")
    status = dispatch("status", {"project": str(project)})
    assert status["result"]["project"]["id"] == "test-project"
    assert status["result"]["unit"]["unit_id"] == json_unit_id(unit)
    assert status["result"]["unit"]["human_gate"]["next_transition"] == "inception"
    assert status["result"]["unit"]["human_gate"]["confirmation_required"] is False

    resumed = dispatch("resume", {"project": str(project)})
    assert resumed["result"]["resume"]["next_action"] == "의도와 인수 조건을 구체화합니다."
    assert resumed["result"]["unit"]["human_gate"]["next_transition"] == "inception"
    assert resumed["result"]["active_unit_binding"]["active"] is True

    verified = dispatch("verify", {"unit": str(unit)})
    assert verified["result"]["valid"] is False
    assert verified["result"]["missing"] == []


def test_runtime_on_activates_project_and_resume_restores_unit(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)

    activated_without_unit = dispatch("on", {"project": str(project)})
    assert activated_without_unit["action"] == "on"
    assert activated_without_unit["result"]["activation"] == "project"
    assert activated_without_unit["result"]["unit"] is None
    assert activated_without_unit["result"]["unit_candidate_details"] == []
    assert activated_without_unit["result"]["active_unit_binding"]["active"] is False
    assert activated_without_unit["result"]["adapter_mode"] == {
        "state": "on",
        "automatic_routing": True,
    }

    first = initialize_unit(project, "Runtime Mode First", project.parent / "units")
    second = initialize_unit(project, "Runtime Mode Second", project.parent / "units")
    before = {
        str(path.relative_to(project.parent)): path.read_bytes()
        for unit in (first, second)
        for path in unit.rglob("*")
        if path.is_file()
    }

    activated_with_multiple_units = dispatch("on", {"project": str(project)})
    result = activated_with_multiple_units["result"]
    assert result["activation"] == "project"
    assert result["unit"] is None
    assert "resume" not in result
    assert {candidate["title"] for candidate in result["unit_candidate_details"]} == {
        "Runtime Mode First",
        "Runtime Mode Second",
    }
    assert all(
        Path(candidate["path"]).name.isascii()
        for candidate in result["unit_candidate_details"]
    )

    with pytest.raises(RuntimeContractError, match="use resume --unit PATH"):
        dispatch("on", {"project": str(project), "unit": str(first)})

    resumed = dispatch(
        "resume",
        {"project": str(project), "unit": str(first)},
    )["result"]
    assert resumed["unit"]["unit_id"] == json_unit_id(first)
    assert resumed["resume"]["next_action"] == "의도와 인수 조건을 구체화합니다."
    assert resumed["active_unit_binding"]["unit"]["path"] == str(first)

    deactivated = dispatch("off")
    assert deactivated["action"] == "off"
    assert deactivated["result"]["adapter_mode"]["state"] == "off"
    assert deactivated["result"]["adapter_mode"]["automatic_routing"] is False
    assert deactivated["result"]["artifacts_changed"] is False
    assert deactivated["result"]["checkpoint_changed"] is False

    after = {
        str(path.relative_to(project.parent)): path.read_bytes()
        for unit in (first, second)
        for path in unit.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_runtime_route_and_compatibility_are_enveloped() -> None:
    route = dispatch("route", {"change": "persistent", "risk": "low"})
    assert route["result"] == {
        "route": "unit",
        "reasons": ["persistent change"],
    }

    compatibility = dispatch("compatibility")
    assert compatibility["action"] == "compatibility"
    assert compatibility["result"]["schema_version"] == "1.0.0"
    assert {runtime["id"] for runtime in compatibility["result"]["runtimes"]} == {
        "kiro",
        "claude",
        "codex",
    }


@pytest.mark.parametrize("field", ["ambiguous", "multi_party", "remote", "sensitive"])
@pytest.mark.parametrize("invalid_value", ["false", 0, []])
def test_runtime_route_rejects_non_boolean_flags(
    field: str,
    invalid_value: object,
) -> None:
    with pytest.raises(RuntimeContractError, match=f"field {field} must be boolean"):
        dispatch(
            "route",
            {"change": "local", "risk": "low", field: invalid_value},
        )


@pytest.mark.parametrize("action", ["evidence", "foundation-evidence"])
@pytest.mark.parametrize("invalid_value", ["true", 1, []])
def test_runtime_evidence_rejects_non_boolean_passed(
    action: str,
    invalid_value: object,
) -> None:
    payload: dict[str, object] = {
        "passed": invalid_value,
        "scope": "boolean contract",
        "recorded_by": "validator",
    }
    if action == "evidence":
        payload.update({"unit": "unused", "commands": []})
    else:
        payload["checks"] = []

    with pytest.raises(RuntimeContractError, match="field passed must be boolean"):
        dispatch(action, payload)


@pytest.mark.parametrize("action", ["evidence", "foundation-evidence"])
def test_runtime_evidence_requires_an_explicit_passed_boolean(action: str) -> None:
    payload: dict[str, object] = {
        "scope": "boolean contract",
        "recorded_by": "validator",
    }
    if action == "evidence":
        payload.update({"unit": "unused", "commands": []})
    else:
        payload["checks"] = []

    with pytest.raises(RuntimeContractError, match="missing runtime request field: passed"):
        dispatch(action, payload)


@pytest.mark.parametrize(
    ("action", "payload", "field"),
    [
        ("handshake", {"runtime": {}, "adapter_version": "1", "protocol_version": "1"}, "runtime"),
        ("route", {"change": ["persistent"]}, "change"),
        (
            "foundation-decision",
            {"outcome": ["approved"], "summary": "ok", "decided_by": "reviewer"},
            "outcome",
        ),
        ("transition", {"unit": 1, "to": "construction"}, "unit"),
    ],
)
def test_runtime_rejects_non_string_string_fields(
    action: str,
    payload: dict[str, object],
    field: str,
) -> None:
    with pytest.raises(
        RuntimeContractError,
        match=rf"runtime request field {field} must be a string",
    ):
        dispatch(action, payload)


def test_runtime_unit_migrate_is_idempotent_for_portable_receipt(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Portable Runtime Unit", project.parent / "units")

    result = dispatch(
        "unit-migrate",
        {"project": str(project), "unit": str(unit)},
    )

    assert result["action"] == "unit-migrate"
    assert result["result"]["migrated"] is False
    assert result["result"]["source_manifest_base"] == "unit"


def test_runtime_rejects_an_explicit_zero_envelope_lifetime(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Zero Envelope Lifetime", project.parent / "units")

    with pytest.raises(AuthorizationError, match="expires_in_hours"):
        dispatch(
            "envelope-propose",
            {
                "unit": str(unit),
                "scope": ["src/**"],
                "stages": [
                    {
                        "name": "construction",
                        "depth": "standard",
                        "allowed_actions": ["edit"],
                    }
                ],
                "allowed_actions": ["edit"],
                "forbidden_actions": ["remote"],
                "max_iterations": 1,
                "proposed_by": "planner-agent",
                "expires_in_hours": 0,
            },
        )


def test_runtime_handshake_fails_closed_without_a_project_lock(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    with pytest.raises(RuntimeContractError, match="installation lock is missing"):
        dispatch(
            "handshake",
            {
                "runtime": "codex",
                "adapter_version": "0.4.0",
                "protocol_version": "1.2.0",
                "project": str(project),
            },
        )
    with pytest.raises(RuntimeContractError, match="incompatible with Core protocol"):
        dispatch(
            "handshake",
            {
                "runtime": "codex",
                "adapter_version": "0.4.0",
                "protocol_version": "2.0.0",
                "project": str(project),
            },
        )


def json_unit_id(unit: Path) -> str:
    import json

    return json.loads((unit / "unit.json").read_text(encoding="utf-8"))["id"]


def test_runtime_decision_and_transition_actions_enforce_gate(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Runtime Decision", project.parent / "units")
    materialize_unit_artifacts(unit)
    dispatch(
        "envelope-propose",
        {
            "unit": str(unit),
            "scope": ["src/**", "tests/**"],
            "stages": [
                {"name": "inception", "depth": "standard", "allowed_actions": ["read"]},
                {
                    "name": "construction",
                    "depth": "standard",
                    "allowed_actions": ["read", "edit", "test"],
                },
            ],
            "allowed_actions": ["read", "edit", "test"],
            "forbidden_actions": ["remote", "deploy", "credential-access"],
            "max_iterations": 5,
            "proposed_by": "planner-agent",
        },
    )

    dispatch("transition", {"unit": str(unit), "to": "inception"})
    dispatch("transition", {"unit": str(unit), "to": "awaiting-inception-decision"})

    decision = dispatch(
        "decision",
        {
            "unit": str(unit),
            "gate": "inception",
            "outcome": "approved",
                "summary": "범위와 인수 조건을 승인한다.",
                "rationale": ["범위가 제한되어 있고 인수 조건을 테스트할 수 있다."],
                "alternatives": [
                    {
                        "option": "Inception을 연기한다.",
                        "reason": "범위가 준비되어 있어 기각했다.",
                    }
                ],
                "tradeoffs": ["승인된 범위는 첫 구현 단계를 제한한다."],
                "risks": ["후속 범위에는 별도의 Unit이 필요할 수 있다."],
            "references": ["acceptance.md", "requirements.md", "execution-envelope.json"],
            "decided_by": "human-reviewer",
        },
    )
    assert decision["action"] == "decision"
    assert decision["result"]["outcome"] == "approved"
    assert decision["result"]["gate"] == "inception"

    transition = dispatch("transition", {"unit": str(unit), "to": "construction"})
    assert transition["result"]["to"] == "construction"


def test_runtime_evidence_action_records_structured_result(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    authorization_id = authorize_test(
        unit,
        target="tests/test_runtime_contract.py",
    )

    evidence = dispatch(
        "evidence",
        {
            "unit": str(unit),
            "passed": True,
            "scope": "runtime evidence contract",
            "recorded_by": "test-validator",
            "commands": [{"authorization_id": authorization_id}],
        },
    )

    assert evidence["action"] == "evidence"
    assert evidence["result"]["passed"] is True
    assert evidence["result"]["command_count"] == 1


def test_runtime_amend_keeps_follow_up_change_in_the_active_unit(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)

    amended = dispatch(
        "amend",
        {
            "unit": str(unit),
            "request": "동일 점수의 결정적 정렬을 추가한다.",
            "reason": "사용자가 활성 Unit 완료 전에 기능을 추가했다.",
            "affected_artifacts": [
                "architecture.md",
                "implementation-guide.md",
            ],
            "requested_by": "human-reviewer",
        },
    )

    assert amended["action"] == "amend"
    assert amended["result"]["status"] == "construction"
    assert amended["result"]["decision_id"].startswith("DEC-")
    assert amended["result"]["amendment_id"].startswith("AMD-")


def test_runtime_init_creates_project_and_project_relative_unit(tmp_path: Path) -> None:
    import shutil

    from test_core_workflow import ROOT

    project_root = tmp_path / "initialized-project"
    project_root.mkdir()
    shutil.copytree(ROOT / "foundation", project_root / "foundation")

    initialized = dispatch(
        "init",
        {
            "path": str(project_root),
            "project_id": "initialized-project",
            "foundation_path": "foundation",
            "profiles": ["software-delivery-profile"],
            "document_language": "ko",
            "maximum_agent_level": "L0",
        },
    )
    project = Path(initialized["result"]["created"])
    assert project == project_root / "project.json"
    assert initialized["result"]["units"] == str(project_root / "units")

    created_unit = dispatch(
        "unit-init",
        {
            "project": str(project),
            "title": "Project Relative Runtime Unit",
            "owner": "test-owner",
        },
    )
    assert Path(created_unit["result"]["created"]).parent == project_root / "units"
