from __future__ import annotations

from pathlib import Path

import pytest

from isekai.workflow import (
    authorize_action,
    initialize_unit,
    propose_execution_envelope,
    record_decision,
    transition_unit,
)

from test_core_workflow import make_project


def envelope_stages() -> list[dict[str, object]]:
    return [
        {"name": "inception", "depth": "standard", "allowed_actions": ["read"]},
        {
            "name": "construction",
            "depth": "standard",
            "allowed_actions": ["read", "edit", "test"],
        },
    ]


def make_enveloped_unit(tmp_path: Path) -> Path:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Adaptive Envelope", project.parent / "units")
    propose_execution_envelope(
        unit,
        scope=["src/**", "tests/**"],
        stages=envelope_stages(),
        allowed_actions=["read", "edit", "test"],
        forbidden_actions=["remote", "deploy", "credential-access"],
        max_iterations=3,
        proposed_by="planner-agent",
    )
    return unit


def approve_inception(unit: Path) -> None:
    transition_unit(unit, "inception")
    transition_unit(unit, "awaiting-inception-decision")
    record_decision(
        unit,
        gate="inception",
        outcome="approved",
        summary="Approve the adaptive construction envelope.",
        rationale=["The scope and construction actions are bounded."],
        alternatives=[
            {"option": "Run without an envelope", "reason": "Rejected because actions need explicit bounds."}
        ],
        tradeoffs=["The envelope limits changes outside src and tests."],
        risks=["Future work may require a new envelope proposal."],
        references=["requirements.md", "execution-envelope.json"],
        decided_by="human-reviewer",
    )
    transition_unit(unit, "construction")


def test_action_is_denied_before_envelope_approval(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)

    result = authorize_action(unit, action="edit", target="src/main.py", stage="construction")

    assert result["allowed"] is False
    assert "not approved" in result["reason"]


def test_approved_envelope_allows_scope_and_action_and_denies_others(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)

    allowed = authorize_action(unit, action="edit", target="src/main.py")
    forbidden = authorize_action(unit, action="deploy", target="src/main.py")
    outside_scope = authorize_action(unit, action="edit", target="docs/README.md")

    assert allowed["allowed"] is True
    assert forbidden["allowed"] is False
    assert "forbidden" in forbidden["reason"]
    assert outside_scope["allowed"] is False
    assert "outside" in outside_scope["reason"]


def test_envelope_proposal_rejects_empty_scope_and_actions(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Invalid Envelope", project.parent / "units")

    with pytest.raises(ValueError, match="Execution Envelope rejected"):
        propose_execution_envelope(
            unit,
            scope=[],
            stages=envelope_stages(),
            allowed_actions=[],
            forbidden_actions=["remote"],
            max_iterations=3,
            proposed_by="planner-agent",
        )
