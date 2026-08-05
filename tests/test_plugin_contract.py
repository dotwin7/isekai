from __future__ import annotations

from pathlib import Path

import pytest

from isekai.plugin_contract import PluginError, dispatch

from test_core_workflow import make_project
from isekai.workflow import initialize_unit


def test_plugin_golden_path_exposes_core_session_contract(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    inception = dispatch("inception", {"project": str(project)})
    assert inception["plugin"] == "isekai-agent-plugin"
    assert inception["action"] == "inception"
    assert inception["result"]["inception"]["decision_required"] is True

    unit = initialize_unit(project, "Plugin Golden Path", project.parent / "units")
    status = dispatch("status", {"project": str(project)})
    assert status["result"]["project"]["id"] == "test-project"
    assert status["result"]["unit"]["unit_id"] == json_unit_id(unit)

    resumed = dispatch("resume", {"project": str(project)})
    assert resumed["result"]["resume"]["next_action"] == "의도와 인수 조건을 구체화합니다."

    verified = dispatch("verify", {"unit": str(unit)})
    assert verified["result"]["valid"] is False
    assert verified["result"]["missing"] == []


def test_plugin_on_activates_project_and_resume_restores_unit(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)

    activated_without_unit = dispatch("on", {"project": str(project)})
    assert activated_without_unit["action"] == "on"
    assert activated_without_unit["result"]["activation"] == "project"
    assert activated_without_unit["result"]["unit"] is None
    assert activated_without_unit["result"]["active_unit"] is None
    assert activated_without_unit["result"]["unit_candidates"] == []
    assert activated_without_unit["result"]["adapter_mode"] == {
        "state": "on",
        "default_state": "off",
        "scope": "conversation",
        "persistent": False,
        "automatic_routing": True,
        "next_session_state": "off",
    }

    first = initialize_unit(project, "Plugin Mode First", project.parent / "units")
    second = initialize_unit(project, "Plugin Mode Second", project.parent / "units")
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
    assert result["active_unit"] is None
    assert "resume" not in result
    assert set(result["unit_candidates"]) == {str(first), str(second)}

    with pytest.raises(PluginError, match="use resume --unit PATH"):
        dispatch("on", {"project": str(project), "unit": str(first)})

    resumed = dispatch(
        "resume",
        {"project": str(project), "unit": str(first)},
    )["result"]
    assert resumed["unit"]["unit_id"] == json_unit_id(first)
    assert resumed["active_unit"]["unit_id"] == json_unit_id(first)
    assert resumed["resume"]["next_action"] == "의도와 인수 조건을 구체화합니다."

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


def test_plugin_route_and_compatibility_are_enveloped() -> None:
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


def test_plugin_handshake_uses_protocol_without_coupling_component_versions() -> None:
    compatible = dispatch(
        "handshake",
        {
            "runtime": "codex",
            "adapter_version": "0.1.0",
            "protocol_version": "1.0.0",
        },
    )

    assert compatible["result"]["compatible"] is True
    assert compatible["core_version"] == "0.1.0"
    assert compatible["protocol_version"] == "1.0.0"
    independently_versioned = dispatch(
        "handshake",
        {
            "runtime": "codex",
            "adapter_version": "0.2.0",
            "protocol_version": "1.0.0",
        },
    )
    assert independently_versioned["result"]["compatible"] is True
    with pytest.raises(PluginError, match="incompatible with Core protocol"):
        dispatch(
            "handshake",
            {
                "runtime": "codex",
                "adapter_version": "0.1.0",
                "protocol_version": "2.0.0",
            },
        )


def json_unit_id(unit: Path) -> str:
    import json

    return json.loads((unit / "unit.json").read_text(encoding="utf-8"))["id"]


def test_plugin_decision_and_transition_actions_enforce_gate(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Plugin Decision", project.parent / "units")
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
            "summary": "Scope and acceptance criteria approved.",
            "rationale": ["The scope is bounded and acceptance criteria are testable."],
            "alternatives": [
                {"option": "Defer inception", "reason": "Rejected because the scope is ready."}
            ],
            "tradeoffs": ["The approved scope limits the first implementation slice."],
            "risks": ["Future scope may require a follow-up Unit."],
            "references": ["acceptance.md", "requirements.md", "execution-envelope.json"],
            "decided_by": "human-reviewer",
        },
    )
    assert decision["action"] == "decision"
    assert decision["result"]["decision"]["outcome"] == "approved"

    transition = dispatch("transition", {"unit": str(unit), "to": "construction"})
    assert transition["result"]["to"] == "construction"


def test_plugin_evidence_action_records_structured_result(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Plugin Evidence", project.parent / "units")

    evidence = dispatch(
        "evidence",
        {
            "unit": str(unit),
            "passed": True,
            "scope": "plugin evidence contract",
            "recorded_by": "test-validator",
            "commands": [
                {
                    "command": "pytest -q",
                    "exit_code": 0,
                    "output_digest": "c" * 64,
                    "observed_at": "2026-08-04T00:00:00+00:00",
                }
            ],
        },
    )

    assert evidence["action"] == "evidence"
    assert evidence["result"]["evidence"]["type"] == "verification-evidence"
    assert evidence["result"]["evidence"]["passed"] is True


def test_plugin_init_creates_project_and_project_relative_unit(tmp_path: Path) -> None:
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
            "title": "Project Relative Plugin Unit",
            "owner": "test-owner",
        },
    )
    assert Path(created_unit["result"]["created"]).parent == project_root / "units"
