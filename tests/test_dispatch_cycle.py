"""End-to-end dispatch cycle verification.

Walks through the full AI-DLC lifecycle and verifies that the dispatcher
produces correct handoffs, selects correct agents/models, detects Human Gates,
and resolves phase contracts at every transition point.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from isekai.dispatch.broker import build_handoff
from isekai.dispatch.config import load_dispatch_config, DEFAULT_DISPATCH
from isekai.dispatch.loop import (
    TERMINAL_STATUSES,
    _build_resume_prompt,
    _select_agent_and_model,
)
from isekai.dispatch.runners import RUNNERS
from isekai.runtime_contract import dispatch
from isekai.workflow import (
    classify_work,
    initialize_unit,
    resolve_context,
    transition_unit,
    RouteRequest,
)

from test_core_workflow import make_project, materialize_unit_artifacts


ROOT = Path(__file__).resolve().parents[1]


def _dispatch(action: str, **payload: object) -> dict:
    return dispatch(action, payload)


def _full_lifecycle_project(tmp_path: Path) -> tuple[Path, Path]:
    """Set up a project and create a Unit ready for the lifecycle."""
    project = make_project(tmp_path)
    project_root = project.parent

    intake = _dispatch(
        "intake",
        project=str(project),
        source="direct-request",
        goal="Dispatch cycle test",
        expected_outcome="Verified dispatch",
        scope=["src/test.py"],
        constraint=[],
        acceptance_criteria=["Tests pass"],
        change="persistent",
        risk="low",
    )
    assert intake["result"]["route"]["route"] == "unit"

    unit = initialize_unit(project, "Dispatch Cycle Test", project_root / "units")
    materialize_unit_artifacts(unit)

    stages = [
        {"name": "inception", "disposition": "apply", "depth": "light", "reason": "test", "allowed_actions": ["read"]},
        {"name": "construction", "disposition": "apply", "depth": "standard", "reason": "test", "allowed_actions": ["read", "edit", "test"]},
        {"name": "validation", "disposition": "apply", "depth": "standard", "reason": "test", "allowed_actions": ["test"]},
        {"name": "release", "disposition": "skip", "depth": "light", "reason": "test", "allowed_actions": []},
        {"name": "operations", "disposition": "skip", "depth": "light", "reason": "test", "allowed_actions": []},
        {"name": "learn", "disposition": "apply", "depth": "light", "reason": "test", "allowed_actions": []},
    ]
    _dispatch(
        "envelope-propose",
        unit=str(unit),
        scope=["src/test.py"],
        stages=stages,
        allowed_actions=["read", "edit", "test"],
        forbidden_actions=[],
        external_access=[],
        max_iterations=10,
        proposed_by="test-dispatcher",
    )

    return project, unit


def test_broker_resolves_phase_at_each_lifecycle_step(tmp_path: Path) -> None:
    project, unit = _full_lifecycle_project(tmp_path)

    # proposed phase → inception
    handoff = build_handoff(unit)
    assert handoff is not None
    assert handoff["phase"] == "inception"
    assert handoff["human_gate_pending"] is False
    assert handoff["stage_skill"] is not None
    assert "inception" in handoff["stage_skill"]
    assert isinstance(handoff["allowed_actions"], list)
    assert len(handoff["allowed_actions"]) > 0

    # transition to inception
    transition_unit(unit, "inception")
    handoff = build_handoff(unit)
    assert handoff["phase"] == "inception"

    # transition to awaiting-inception-decision → Human Gate
    transition_unit(unit, "awaiting-inception-decision")
    handoff = build_handoff(unit)
    assert handoff["phase"] == "inception"
    assert handoff["human_gate_pending"] is True

    # record inception decision and transition to construction
    _dispatch(
        "decision",
        unit=str(unit),
        gate="inception",
        outcome="approved",
        summary="디스패치 테스트를 승인한다.",
        rationale=["디스패치 사이클 검증을 위한 승인"],
        alternatives=[],
        tradeoffs=[],
        risks=[],
        references=["execution-envelope.json", "intent.md", "requirements.md", "plan.md"],
        decided_by="human-tester",
    )
    transition_unit(unit, "construction")
    handoff = build_handoff(unit)
    assert handoff["phase"] == "construction"
    assert handoff["human_gate_pending"] is False
    assert "construction" in handoff["stage_skill"]

    # transition to validation
    _dispatch(
        "decision",
        unit=str(unit),
        gate="architecture",
        outcome="approved",
        summary="아키텍처 구현을 승인한다.",
        rationale=["디스패치 사이클 검증을 위한 아키텍처 승인"],
        alternatives=[],
        tradeoffs=[],
        risks=[],
        references=["architecture.md", "implementation-guide.md"],
        decided_by="human-tester",
    )
    transition_unit(unit, "validation")
    handoff = build_handoff(unit)
    assert handoff["phase"] == "validation"
    assert "validation" in handoff["stage_skill"]
    assert any(c.get("id") == "tests" for c in handoff["checks"])


def test_agent_model_selection_per_phase(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config = load_dispatch_config(project_root)

    inception_agent, inception_model = _select_agent_and_model(config, "inception")
    construction_agent, construction_model = _select_agent_and_model(config, "construction")
    validation_agent, validation_model = _select_agent_and_model(config, "validation")

    # defaults: inception=strong, construction=fast, validation=strong
    assert inception_agent == "claude"
    assert "opus" in inception_model
    assert construction_agent == "claude"
    assert "sonnet" in construction_model
    assert validation_agent == "claude"
    assert "opus" in validation_model


def test_model_escalation_on_failure(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config = load_dispatch_config(project_root)

    # no escalation at 0 failures
    _, model_0 = _select_agent_and_model(config, "construction", consecutive_failures=0)
    assert "sonnet" in model_0

    # escalation at 2+ failures
    _, model_2 = _select_agent_and_model(config, "construction", consecutive_failures=2)
    assert "opus" in model_2


def test_custom_dispatch_config(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    isekai_dir = project_root / ".isekai"
    isekai_dir.mkdir(parents=True)
    (isekai_dir / "dispatch.json").write_text(json.dumps({
        "default_agent": "codex",
        "phase_dispatch": {
            "construction": {"agent": "codex", "model": "o4-mini"},
        },
    }), encoding="utf-8")

    config = load_dispatch_config(project_root)
    agent, model = _select_agent_and_model(config, "construction")
    assert agent == "codex"
    assert model == "o4-mini"


def test_resume_prompt_includes_skill_and_instructions() -> None:
    handoff = {
        "unit_id": "TEST-001",
        "phase": "construction",
        "next_action": "Implement sort function",
        "pending": ["write tests"],
        "completed": ["create file"],
    }
    skill_content = "# Construction\n\nBuild stuff."
    prompt = _build_resume_prompt(handoff, skill_content)

    assert "TEST-001" in prompt
    assert "construction" in prompt
    assert "Implement sort function" in prompt
    assert "/isekai on" in prompt
    assert "/isekai resume" in prompt
    assert "Construction" in prompt


def test_terminal_status_detected() -> None:
    assert "learned" in TERMINAL_STATUSES
    assert "abandoned" in TERMINAL_STATUSES
    assert "construction" not in TERMINAL_STATUSES


def test_runners_registered() -> None:
    assert "claude" in RUNNERS
    assert "codex" in RUNNERS
    assert "kiro" in RUNNERS


def test_claude_runner_builds_correct_command(tmp_path: Path) -> None:
    runner = RUNNERS["claude"]()
    cmd = runner.build_command(
        tmp_path, model="claude-opus-4-8", prompt="do work", max_turns=30,
    )
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "do work" in cmd
    assert "--max-turns" in cmd
    assert "30" in cmd
    assert "--model" in cmd
    assert "claude-opus-4-8" in cmd
    assert "--project-dir" in cmd


def test_codex_runner_builds_correct_command(tmp_path: Path) -> None:
    runner = RUNNERS["codex"]()
    cmd = runner.build_command(
        tmp_path, model="o4-mini", prompt="do work", max_turns=30,
    )
    assert cmd[0] == "codex"
    assert "--quiet" in cmd
    assert "--model" in cmd
    assert "o4-mini" in cmd
    assert "do work" in cmd


def test_kiro_runner_builds_correct_command(tmp_path: Path) -> None:
    runner = RUNNERS["kiro"]()
    cmd = runner.build_command(
        tmp_path, prompt="do work", max_turns=30,
    )
    assert cmd[0] == "kiro"
    assert "do work" in cmd


def test_cross_agent_dispatch_config(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    isekai_dir = project_root / ".isekai"
    isekai_dir.mkdir(parents=True)
    (isekai_dir / "dispatch.json").write_text(json.dumps({
        "phase_dispatch": {
            "inception": {"agent": "claude", "model": "claude-opus-5"},
            "construction": {"agent": "codex", "model": "o4-mini"},
            "validation": {"agent": "claude", "model": "claude-opus-4-8"},
            "release": {"agent": "kiro"},
        },
    }), encoding="utf-8")

    config = load_dispatch_config(project_root)

    agent, model = _select_agent_and_model(config, "inception")
    assert agent == "claude"
    assert model == "claude-opus-5"

    agent, model = _select_agent_and_model(config, "construction")
    assert agent == "codex"
    assert model == "o4-mini"

    agent, model = _select_agent_and_model(config, "validation")
    assert agent == "claude"
    assert model == "claude-opus-4-8"

    agent, model = _select_agent_and_model(config, "release")
    assert agent == "kiro"


def test_codex_escalation(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    isekai_dir = project_root / ".isekai"
    isekai_dir.mkdir(parents=True)
    (isekai_dir / "dispatch.json").write_text(json.dumps({
        "phase_dispatch": {
            "construction": {"agent": "codex", "tier": "fast"},
        },
    }), encoding="utf-8")

    config = load_dispatch_config(project_root)

    _, model_0 = _select_agent_and_model(config, "construction", consecutive_failures=0)
    assert "mini" in model_0 or "fast" in model_0

    _, model_2 = _select_agent_and_model(config, "construction", consecutive_failures=2)
    assert "o3" in model_2
