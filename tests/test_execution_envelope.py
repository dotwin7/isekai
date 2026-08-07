from __future__ import annotations

import json
from pathlib import Path

import pytest

from isekai.workflow import (
    authorize_action,
    initialize_unit,
    propose_execution_envelope,
    record_decision,
    transition_unit,
)
from isekai.workflow.unit.execution import _scope_pattern_matches

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


def test_scope_wildcards_stay_within_one_path_segment() -> None:
    assert _scope_pattern_matches("src/*.py", "src/main.py") is True
    assert _scope_pattern_matches("src/*.py", "src/vendor/deep.py") is False
    assert _scope_pattern_matches("src/**", "src/vendor/deep.py") is True
    assert _scope_pattern_matches("src/**", "src") is True
    assert _scope_pattern_matches("src/**/fixtures/*.json", "src/fixtures/a.json") is True
    assert _scope_pattern_matches("src/**/fixtures/*.json", "src/a/b/fixtures/c.json") is True
    assert _scope_pattern_matches("src/**/fixtures/*.json", "src/fixtures/nested/d.json") is False
    assert _scope_pattern_matches("**", "any/depth/of/path.txt") is True
    assert _scope_pattern_matches("?rc/main.py", "src/main.py") is True
    assert _scope_pattern_matches("s?c", "s/c") is False


def test_single_star_scope_denies_paths_below_the_matched_segment(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Narrow Scope", project.parent / "units")
    propose_execution_envelope(
        unit,
        scope=["src/*.py"],
        stages=envelope_stages(),
        allowed_actions=["read", "edit", "test"],
        forbidden_actions=["remote", "deploy", "credential-access"],
        max_iterations=3,
        proposed_by="planner-agent",
    )
    approve_inception(unit)

    direct = authorize_action(unit, action="edit", target="src/main.py")
    nested = authorize_action(unit, action="edit", target="src/vendor/deep.py")

    assert direct["allowed"] is True
    assert nested["allowed"] is False
    assert "outside" in nested["reason"]


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


def test_envelope_rejects_actions_outside_the_local_agent_contract(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Unsafe Envelope", project.parent / "units")

    with pytest.raises(ValueError, match="unsupported|prohibited"):
        propose_execution_envelope(
            unit,
            scope=["src/**"],
            stages=[
                {
                    "name": "construction",
                    "depth": "standard",
                    "allowed_actions": ["read", "deploy"],
                }
            ],
            allowed_actions=["read", "deploy"],
            forbidden_actions=[],
            max_iterations=3,
            proposed_by="planner-agent",
        )


def test_later_rejected_inception_decision_revokes_envelope_authorization(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    record_decision(
        unit,
        gate="inception",
        outcome="rejected",
        summary="Revoke the previously approved execution scope.",
        rationale=["The approved scope is no longer valid."],
        alternatives=[
            {"option": "Keep the approval", "reason": "Rejected because the scope changed."}
        ],
        tradeoffs=["Construction pauses until a new approval is recorded."],
        risks=["Continuing would execute against revoked authority."],
        references=["execution-envelope.json"],
        decided_by="human-reviewer",
    )

    result = authorize_action(unit, action="edit", target="src/main.py")

    assert result["allowed"] is False
    assert "revoked" in result["reason"] or "latest" in result["reason"]


def test_replaced_envelope_cannot_reuse_an_earlier_inception_approval(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    transition_unit(unit, "inception")
    transition_unit(unit, "awaiting-inception-decision")
    record_decision(
        unit,
        gate="inception",
        outcome="approved",
        summary="Approve the narrow execution scope.",
        rationale=["The reviewed scope is limited to source and tests."],
        alternatives=[],
        tradeoffs=[],
        risks=[],
        references=["execution-envelope.json"],
        decided_by="human-reviewer",
    )

    propose_execution_envelope(
        unit,
        scope=["**"],
        stages=envelope_stages(),
        allowed_actions=["read", "edit", "test"],
        forbidden_actions=["remote", "deploy", "credential-access"],
        max_iterations=99,
        proposed_by="planner-agent",
    )

    with pytest.raises(ValueError, match="replaced|changed"):
        transition_unit(unit, "construction")


def test_authorization_rejects_project_escape_and_stage_spoofing(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)

    traversal = authorize_action(
        unit, action="edit", target="src/../../outside.txt"
    )
    spoofed_stage = authorize_action(
        unit, action="read", target="src/main.py", stage="inception"
    )

    assert traversal["allowed"] is False
    assert "escapes" in traversal["reason"]
    assert spoofed_stage["allowed"] is False
    assert "does not match" in spoofed_stage["reason"]


def test_authorization_grants_consume_the_iteration_budget(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Iteration Budget", project.parent / "units")
    propose_execution_envelope(
        unit,
        scope=["src/**"],
        stages=[
            {
                "name": "construction",
                "depth": "standard",
                "allowed_actions": ["edit"],
            }
        ],
        allowed_actions=["edit"],
        forbidden_actions=["remote"],
        max_iterations=1,
        proposed_by="planner-agent",
    )
    approve_inception(unit)

    first = authorize_action(unit, action="edit", target="src/first.py")
    second = authorize_action(unit, action="edit", target="src/second.py")
    ledger = json.loads(
        (unit / "execution-authorizations.json").read_text(encoding="utf-8")
    )

    assert first["allowed"] is True
    assert first["remaining_iterations"] == 0
    assert second["allowed"] is False
    assert "exhausted" in second["reason"]
    assert len(ledger["grants"]) == 1


def test_authorization_cannot_edit_its_own_control_ledger(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Protected Ledger", project.parent / "units")
    propose_execution_envelope(
        unit,
        scope=["**"],
        stages=[
            {
                "name": "construction",
                "depth": "standard",
                "allowed_actions": ["edit"],
            }
        ],
        allowed_actions=["edit"],
        forbidden_actions=["remote"],
        max_iterations=2,
        proposed_by="planner-agent",
    )
    approve_inception(unit)
    ledger_target = str(
        (unit / "execution-authorizations.json").relative_to(project.parent)
    )

    result = authorize_action(unit, action="edit", target=ledger_target)

    assert result["allowed"] is False
    assert "control artifact" in result["reason"]
