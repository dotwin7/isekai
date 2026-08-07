from __future__ import annotations

import json
from pathlib import Path

import pytest

from isekai.jsonio import write_json_atomic
from isekai.workflow import (
    DECISION_REQUIRED_FIELDS,
    authorize_action,
    build_command_evidence,
    initialize_unit,
    propose_execution_envelope,
    record_decision,
    record_evidence,
    transition_unit,
    verify_unit,
)
from isekai.session import update_checkpoint

from test_core_workflow import make_project


def make_unit(tmp_path: Path) -> Path:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Decision Lifecycle", project.parent / "units")
    propose_execution_envelope(
        unit,
        scope=["src/**", "tests/**"],
        stages=[
            {"name": "inception", "depth": "standard", "allowed_actions": ["read"]},
            {
                "name": "construction",
                "depth": "standard",
                "allowed_actions": ["read", "edit", "test"],
            },
            {"name": "operations", "depth": "light", "allowed_actions": ["read", "test"]},
        ],
        allowed_actions=["read", "edit", "test"],
        forbidden_actions=["remote", "deploy", "credential-access"],
        max_iterations=5,
        proposed_by="planner-agent",
    )
    return unit


def approve(unit: Path, gate: str) -> None:
    references = ["tests/test_decision_lifecycle.py", "execution-envelope.json"]
    if gate == "release":
        references.append("evidence/verification.json")
    record_decision(
        unit,
        gate=gate,
        outcome="approved",
        summary=f"Approve {gate} gate for the test Unit.",
        rationale=[f"The {gate} gate criteria are understood and satisfied for this test."],
        alternatives=[{"option": "Defer the gate", "reason": "Rejected because the test gate is ready."}],
        tradeoffs=["The test records a minimal but explicit Decision Packet."],
        risks=["This is test-only evidence."],
        references=references,
        decided_by="human-reviewer",
    )


def start_construction(unit: Path) -> None:
    transition_unit(unit, "inception")
    transition_unit(unit, "awaiting-inception-decision")
    approve(unit, "inception")
    transition_unit(unit, "construction")


def authorize_test(unit: Path) -> str:
    authorization = authorize_action(
        unit,
        action="test",
        target="tests/test_decision_lifecycle.py",
    )
    assert authorization["allowed"] is True
    return str(authorization["authorization_id"])


def complete_acceptance(unit: Path) -> None:
    (unit / "acceptance.md").write_text(
        "# Acceptance Criteria\n\n- [x] Lifecycle behavior is verified.\n",
        encoding="utf-8",
    )


def passing_evidence(unit: Path) -> None:
    authorization_id = authorize_test(unit)
    record_evidence(
        unit,
        passed=True,
        scope="Core and plugin lifecycle tests",
        recorded_by="test-validator",
        commands=[
            {
                "command": "PYTHONPATH=src python3 -m pytest -q",
                "exit_code": 0,
                "output_digest": "a" * 64,
                "observed_at": "2026-08-04T00:00:00+00:00",
                "authorization_id": authorization_id,
            }
        ],
    )


def test_full_lifecycle_requires_the_expected_human_decisions(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)

    transition_unit(unit, "inception")
    transition_unit(unit, "awaiting-inception-decision")
    with pytest.raises(ValueError, match="approved inception Decision"):
        transition_unit(unit, "construction")

    approve(unit, "inception")
    transition_unit(unit, "construction")
    with pytest.raises(ValueError, match="approved architecture Decision"):
        transition_unit(unit, "awaiting-release-decision")

    approve(unit, "architecture")
    transition_unit(unit, "awaiting-release-decision")
    with pytest.raises(ValueError, match="approved release Decision"):
        transition_unit(unit, "releasing")

    passing_evidence(unit)
    approve(unit, "release")
    with pytest.raises(ValueError, match="acceptance criteria remain unchecked"):
        transition_unit(unit, "releasing")
    (unit / "acceptance.md").write_text(
        "# Acceptance Criteria\n\n* [ ] Alternate Markdown bullet remains open.\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="acceptance criteria remain unchecked"):
        transition_unit(unit, "releasing")
    (unit / "acceptance.md").write_text("# Acceptance Criteria\n", encoding="utf-8")
    with pytest.raises(ValueError, match="acceptance criteria are missing"):
        transition_unit(unit, "releasing")
    complete_acceptance(unit)
    architecture = unit / "architecture.md"
    architecture_content = architecture.read_text(encoding="utf-8")
    architecture.unlink()
    with pytest.raises(ValueError, match="required Unit artifacts are missing"):
        transition_unit(unit, "releasing")
    architecture.write_text(architecture_content, encoding="utf-8")
    transition_unit(unit, "releasing")
    transition_unit(unit, "operating")
    with pytest.raises(ValueError, match="approved operation Decision"):
        transition_unit(unit, "learned")

    # Operations may add new authorized work and replace the current Evidence.
    # The historical Release Decision stays bound to the release Evidence, while
    # the learned transition validates the new, current operations Evidence.
    passing_evidence(unit)
    assert verify_unit(unit)["valid"] is True
    approve(unit, "operation")
    with pytest.raises(ValueError, match="pending work"):
        transition_unit(unit, "learned")
    update_checkpoint(
        unit,
        completed=["Lifecycle implementation and verification"],
        pending=[],
        blocked_by=[],
        next_action="Unit complete",
    )
    result = transition_unit(unit, "learned")

    assert result["from"] == "operating"
    assert result["to"] == "learned"
    assert result["phase"] == "operations"

    decisions = json.loads((unit / "decisions.json").read_text(encoding="utf-8"))
    assert len(decisions["decisions"]) == 4
    assert all(DECISION_REQUIRED_FIELDS <= decision.keys() for decision in decisions["decisions"])


def test_operating_verify_rejects_a_tampered_release_evidence_binding(
    tmp_path: Path,
) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    approve(unit, "architecture")
    transition_unit(unit, "awaiting-release-decision")
    passing_evidence(unit)
    approve(unit, "release")
    complete_acceptance(unit)
    transition_unit(unit, "releasing")
    transition_unit(unit, "operating")

    path = unit / "decisions.json"
    decisions = json.loads(path.read_text(encoding="utf-8"))
    release = next(
        decision for decision in decisions["decisions"] if decision["gate"] == "release"
    )
    release["approval_subject"]["digest"] = "sha256:" + "0" * 64
    write_json_atomic(path, decisions)

    result = verify_unit(unit)

    assert result["valid"] is False
    assert any(
        "Release Decision digest does not match" in issue for issue in result["issues"]
    )


def test_approved_inception_decision_metadata_is_digest_bound(tmp_path: Path) -> None:
    from isekai.workflow import _decision_record_digest

    unit = make_unit(tmp_path)
    start_construction(unit)
    decisions_path = unit / "decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    decision = decisions["decisions"][-1]
    decision["decided_by"] = "different-actor"
    decision["summary"] = "Rewritten after approval."
    # Recomputing the self-digest must not bypass the independent Envelope binding.
    decision["decision_digest"] = _decision_record_digest(decision)
    write_json_atomic(decisions_path, decisions)

    authorization = authorize_action(unit, action="edit", target="src/main.py")
    result = verify_unit(unit)

    assert authorization["allowed"] is False
    assert "approval digest" in authorization["reason"]
    assert any("approval digest" in issue for issue in result["issues"])


def test_release_rejects_evidence_made_stale_by_a_later_authorization(
    tmp_path: Path,
) -> None:
    unit = make_unit(tmp_path)
    transition_unit(unit, "inception")
    transition_unit(unit, "awaiting-inception-decision")
    approve(unit, "inception")
    transition_unit(unit, "construction")
    approve(unit, "architecture")
    transition_unit(unit, "awaiting-release-decision")
    passing_evidence(unit)
    approve(unit, "release")

    authorization = authorize_action(
        unit,
        action="edit",
        target="src/after-verification.py",
    )

    assert authorization["allowed"] is True
    with pytest.raises(ValueError, match="passing verification Evidence"):
        transition_unit(unit, "releasing")
    assert any("stale" in issue for issue in verify_unit(unit)["issues"])

    passing_evidence(unit)
    with pytest.raises(ValueError, match="Release Decision"):
        transition_unit(unit, "releasing")
    approve(unit, "release")
    complete_acceptance(unit)
    assert transition_unit(unit, "releasing")["to"] == "releasing"


def test_latest_rejected_decision_blocks_a_previous_approval(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    transition_unit(unit, "inception")
    transition_unit(unit, "awaiting-inception-decision")

    approve(unit, "inception")
    record_decision(
        unit,
        gate="inception",
        outcome="rejected",
        summary="The scope needs revision before approval.",
        rationale=["The current scope is too broad for this gate."],
        alternatives=[{"option": "Approve now", "reason": "Rejected because scope is not bounded."}],
        tradeoffs=["Deferring approval delays construction."],
        risks=["Proceeding would leave scope ambiguity."],
        references=["tests/test_decision_lifecycle.py"],
        decided_by="human-reviewer",
    )

    with pytest.raises(ValueError, match="approved inception Decision"):
        transition_unit(unit, "construction")

    approve(unit, "inception")
    transition_unit(unit, "construction")


def test_invalid_skip_transition_is_rejected(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)

    with pytest.raises(ValueError, match="invalid lifecycle transition"):
        transition_unit(unit, "construction")


@pytest.mark.parametrize("gate", ["architecture", "release", "operation", "knowledge"])
def test_decisions_cannot_be_preapproved_before_their_gate(
    tmp_path: Path,
    gate: str,
) -> None:
    unit = make_unit(tmp_path)

    with pytest.raises(ValueError, match=f"{gate} Decision cannot be recorded"):
        approve(unit, gate)


def test_evidence_requires_a_current_test_authorization(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    with pytest.raises(ValueError, match="after Construction"):
        record_evidence(
            unit,
            passed=True,
            scope="premature evidence",
            recorded_by="test-validator",
            commands=[
                {
                    "command": "not actually run",
                    "exit_code": 0,
                    "output_digest": "a" * 64,
                    "observed_at": "2026-08-04T00:00:00+00:00",
                    "authorization_id": "AUTH-NOT-GRANTED",
                }
            ],
        )
    start_construction(unit)
    read_authorization = authorize_action(unit, action="read", target="src/main.py")
    assert read_authorization["allowed"] is True

    with pytest.raises(ValueError, match="current test authorization"):
        record_evidence(
            unit,
            passed=True,
            scope="forged evidence",
            recorded_by="test-validator",
            commands=[
                {
                    "command": "not actually run",
                    "exit_code": 0,
                    "output_digest": "a" * 64,
                    "observed_at": "2026-08-04T00:00:00+00:00",
                    "authorization_id": read_authorization["authorization_id"],
                }
            ],
        )


def test_evidence_rejects_a_forged_out_of_scope_authorization_grant(
    tmp_path: Path,
) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    envelope = json.loads(
        (unit / "execution-envelope.json").read_text(encoding="utf-8")
    )
    ledger_path = unit / "execution-authorizations.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["grants"].append(
        {
            "id": "AUTH-FORGED",
            "action": "test",
            "target": "secrets/outside.txt",
            "stage": "construction",
            "iteration": 1,
            "decision_id": "DEC-WRONG",
            "envelope_digest": envelope["approval_digest"],
            "authorized_at": "2026-08-05T00:00:00+00:00",
        }
    )
    write_json_atomic(ledger_path, ledger)

    with pytest.raises(ValueError, match="Action ledger blocks Evidence") as error:
        record_evidence(
            unit,
            passed=True,
            scope="forged ledger probe",
            recorded_by="test-validator",
            commands=[
                {
                    "command": "not actually run",
                    "exit_code": 0,
                    "output_digest": "a" * 64,
                    "observed_at": "2026-08-05T00:00:00+00:00",
                    "authorization_id": "AUTH-FORGED",
                }
            ],
        )

    assert "outside the Execution Envelope scope" in str(error.value)
    assert "approval Decision" in str(error.value)


def test_evidence_cannot_rebind_an_old_test_grant_after_an_edit(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)
    command = {
        "command": "pytest -q",
        "exit_code": 0,
        "output_digest": "a" * 64,
        "observed_at": "2026-08-04T00:00:00+00:00",
        "authorization_id": authorization_id,
    }
    record_evidence(
        unit,
        passed=True,
        scope="initial verification",
        recorded_by="test-validator",
        commands=[command],
    )
    edit = authorize_action(unit, action="edit", target="src/after-test.py")
    assert edit["allowed"] is True

    with pytest.raises(ValueError, match="latest authorized test actions"):
        record_evidence(
            unit,
            passed=True,
            scope="stale verification",
            recorded_by="test-validator",
            commands=[command],
        )


def test_failed_evidence_is_auditable_but_does_not_enable_release(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)
    result = record_evidence(
        unit,
        passed=False,
        scope="intentional failure case",
        recorded_by="test-validator",
        commands=[
            {
                "command": "python failing-check.py",
                "exit_code": 1,
                "output_digest": "b" * 64,
                "observed_at": "2026-08-04T00:00:00+00:00",
                "authorization_id": authorization_id,
            }
        ],
    )

    assert result["evidence"]["passed"] is False
    verification = verify_unit(unit)
    assert "verification evidence is not passing" in verification["issues"]


def test_evidence_rejects_missing_output_digest_provenance(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)

    with pytest.raises(ValueError, match="output_digest"):
        record_evidence(
            unit,
            passed=True,
            scope="invalid evidence",
            recorded_by="test-validator",
            commands=[
                {
                    "command": "pytest -q",
                    "exit_code": 0,
                    "output_digest": "not-a-digest",
                    "observed_at": "2026-08-04T00:00:00+00:00",
                    "authorization_id": authorization_id,
                }
            ],
        )


def test_evidence_rejects_invalid_observation_timestamp(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)

    with pytest.raises(ValueError, match="observed_at"):
        record_evidence(
            unit,
            passed=True,
            scope="invalid evidence timestamp",
            recorded_by="test-validator",
            commands=[
                {
                    "command": "pytest -q",
                    "exit_code": 0,
                    "output_digest": "a" * 64,
                    "observed_at": "not-a-timestamp",
                    "authorization_id": authorization_id,
                }
            ],
        )


def test_command_evidence_digest_is_derived_from_output(tmp_path: Path) -> None:
    command = build_command_evidence(
        "pytest -q",
        0,
        "all tests passed",
        "2026-08-04T00:00:00+00:00",
    )
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)
    result = record_evidence(
        unit,
        passed=True,
        scope="digest helper",
        recorded_by="test-validator",
        commands=[
            {
                "command": command["command"],
                "exit_code": command["exit_code"],
                "output": "all tests passed",
                "observed_at": command["observed_at"],
                "authorization_id": authorization_id,
            }
        ],
    )

    assert result["evidence"]["commands"][0]["output_digest"] == command["output_digest"]


def test_operating_transition_rejects_evidence_staled_during_release(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Release Mutation", project.parent / "units")
    propose_execution_envelope(
        unit,
        scope=["src/**", "tests/**"],
        stages=[
            {"name": "inception", "depth": "standard", "allowed_actions": ["read"]},
            {
                "name": "construction",
                "depth": "standard",
                "allowed_actions": ["read", "edit", "test"],
            },
            {
                "name": "release",
                "depth": "standard",
                "allowed_actions": ["read", "edit", "test"],
            },
        ],
        allowed_actions=["read", "edit", "test"],
        forbidden_actions=["remote", "deploy", "credential-access"],
        max_iterations=5,
        proposed_by="planner-agent",
    )
    start_construction(unit)
    approve(unit, "architecture")
    transition_unit(unit, "awaiting-release-decision")
    passing_evidence(unit)
    approve(unit, "release")
    complete_acceptance(unit)
    transition_unit(unit, "releasing")

    authorization = authorize_action(
        unit,
        action="edit",
        target="src/after-release.py",
    )

    assert authorization["allowed"] is True
    assert any("stale" in issue for issue in verify_unit(unit)["issues"])
    with pytest.raises(ValueError, match="passing verification Evidence"):
        transition_unit(unit, "operating")
    assert json.loads((unit / "unit.json").read_text(encoding="utf-8"))["status"] == "releasing"


def test_decision_packet_requires_rationale_and_explained_alternatives(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    with pytest.raises(ValueError, match="Decision Packet rejected"):
        record_decision(
            unit,
            gate="architecture",
            outcome="approved",
            summary="Incomplete packet.",
            rationale=[],
            alternatives=[{"option": "Missing reason"}],
            tradeoffs=[],
            risks=[],
            references=[],
            decided_by="human-reviewer",
        )

    with pytest.raises(ValueError, match="needs reason"):
        record_decision(
            unit,
            gate="architecture",
            outcome="approved",
            summary="Alternative is unexplained.",
            rationale=["The selected design meets the requirement."],
            alternatives=[{"option": "Unexplained alternative", "reason": ""}],
            tradeoffs=[],
            risks=[],
            references=[],
            decided_by="human-reviewer",
        )


def test_foreign_unit_decision_cannot_satisfy_a_gate(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    first = initialize_unit(project, "First Decision Unit", project.parent / "units")
    second = initialize_unit(project, "Second Decision Unit", project.parent / "units")
    for unit in (first, second):
        propose_execution_envelope(
            unit,
            scope=["src/**"],
            stages=[
                {"name": "inception", "depth": "standard", "allowed_actions": ["read"]},
                {"name": "construction", "depth": "standard", "allowed_actions": ["read", "edit"]},
            ],
            allowed_actions=["read", "edit"],
            forbidden_actions=["remote", "deploy", "credential-access"],
            max_iterations=2,
            proposed_by="planner-agent",
        )
        transition_unit(unit, "inception")
        transition_unit(unit, "awaiting-inception-decision")

    approve(first, "inception")
    first_decisions = json.loads((first / "decisions.json").read_text(encoding="utf-8"))
    second_decisions = json.loads((second / "decisions.json").read_text(encoding="utf-8"))
    second_decisions["decisions"] = first_decisions["decisions"]
    (second / "decisions.json").write_text(
        json.dumps(second_decisions, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="approved inception Decision"):
        transition_unit(second, "construction")
