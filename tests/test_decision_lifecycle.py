from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from isekai.jsonio import write_json_atomic
from isekai.workflow.errors import (
    AuthorizationError,
    EvidenceError,
    IntegrityError,
    LifecycleError,
)
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
            {
                "name": "validation",
                "depth": "standard",
                "allowed_actions": ["read", "test"],
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
        summary=f"테스트 Unit의 {gate} Gate를 승인한다.",
        rationale=[f"이 테스트에서 {gate} Gate 조건을 이해하고 충족했다."],
        alternatives=[
            {
                "option": "Gate 결정을 연기한다.",
                "reason": "테스트 Gate가 준비되어 기각했다.",
            }
        ],
        tradeoffs=["최소 범위지만 명시적인 Decision Packet을 기록한다."],
        risks=["테스트 전용 Evidence다."],
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
        "# 인수 조건\n\n- [x] 생명주기 동작을 검증했다.\n",
        encoding="utf-8",
    )


def test_record_rejects_english_decision_descriptions_in_korean_unit(
    tmp_path: Path,
) -> None:
    unit = make_unit(tmp_path)
    transition_unit(unit, "inception")
    transition_unit(unit, "awaiting-inception-decision")
    ledger_before = (unit / "decisions.json").read_bytes()

    with pytest.raises(IntegrityError, match="summary must use Korean"):
        record_decision(
            unit,
            gate="inception",
            outcome="approved",
            summary="Approve the English Decision Packet.",
            rationale=["The text is intentionally written only in English."],
            alternatives=[
                {"option": "Defer", "reason": "Rejected for the language regression."}
            ],
            tradeoffs=["This packet is invalid for a Korean Unit."],
            risks=["A reviewer cannot rely on the configured document language."],
            references=["execution-envelope.json"],
            decided_by="human-reviewer",
        )

    assert (unit / "decisions.json").read_bytes() == ledger_before


def passing_evidence(unit: Path) -> None:
    authorization_id = authorize_test(unit)
    record_evidence(
        unit,
        passed=True,
        scope="Core and runtime lifecycle tests",
        recorded_by="test-validator",
        commands=[
            {
                "command": "PYTHONPATH=src python3 -m pytest -q",
                "exit_code": 0,
                "output_digest": "a" * 64,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "authorization_id": authorization_id,
            }
        ],
    )


def test_evidence_record_rejects_symlinked_records_directory(
    tmp_path: Path,
) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)
    external = tmp_path / "external-evidence-records"
    external.mkdir()
    (unit / "evidence/records").symlink_to(external, target_is_directory=True)

    with pytest.raises(IntegrityError, match="path contains a symlink"):
        record_evidence(
            unit,
            passed=True,
            scope="Evidence path boundary regression",
            recorded_by="test-validator",
            commands=[
                {
                    "command": "pytest -q",
                    "exit_code": 0,
                    "output_digest": "a" * 64,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "authorization_id": authorization_id,
                }
            ],
        )

    assert list(external.iterdir()) == []


def test_full_lifecycle_requires_the_expected_human_decisions(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)

    transition_unit(unit, "inception")
    transition_unit(unit, "awaiting-inception-decision")
    with pytest.raises(LifecycleError, match="approved inception Decision"):
        transition_unit(unit, "construction")

    approve(unit, "inception")
    transition_unit(unit, "construction")
    with pytest.raises(LifecycleError, match="approved architecture Decision"):
        transition_unit(unit, "validation")

    approve(unit, "architecture")
    transition_unit(unit, "validation")
    transition_unit(unit, "awaiting-release-decision")
    with pytest.raises(LifecycleError, match="approved release Decision"):
        transition_unit(unit, "releasing")

    passing_evidence(unit)
    approve(unit, "release")
    with pytest.raises(LifecycleError, match="acceptance criteria remain unchecked"):
        transition_unit(unit, "releasing")
    (unit / "acceptance.md").write_text(
        "# Acceptance Criteria\n\n* [ ] Alternate Markdown bullet remains open.\n",
        encoding="utf-8",
    )
    with pytest.raises(LifecycleError, match="acceptance criteria remain unchecked"):
        transition_unit(unit, "releasing")
    (unit / "acceptance.md").write_text("# Acceptance Criteria\n", encoding="utf-8")
    with pytest.raises(LifecycleError, match="acceptance criteria are missing"):
        transition_unit(unit, "releasing")
    complete_acceptance(unit)
    architecture = unit / "architecture.md"
    architecture_content = architecture.read_text(encoding="utf-8")
    architecture.unlink()
    with pytest.raises(LifecycleError, match="required Unit artifacts are missing"):
        transition_unit(unit, "releasing")
    architecture.write_text(architecture_content, encoding="utf-8")
    criteria_path = unit / "evaluations/criteria.json"
    criteria = json.loads(criteria_path.read_text(encoding="utf-8"))
    criteria["visibility"] = "public"
    write_json_atomic(criteria_path, criteria)
    with pytest.raises(LifecycleError, match="evaluation criteria must be evaluation-only"):
        transition_unit(unit, "releasing")
    criteria["visibility"] = "evaluation-only"
    write_json_atomic(criteria_path, criteria)
    transition_unit(unit, "releasing")
    transition_unit(unit, "operating")
    with pytest.raises(LifecycleError, match="approved operation Decision"):
        transition_unit(unit, "learned")

    # Operations may add new authorized work and replace the current Evidence.
    # The historical Release Decision stays bound to the release Evidence, while
    # the learned transition validates the new, current operations Evidence.
    passing_evidence(unit)
    assert verify_unit(unit)["valid"] is True
    approve(unit, "operation")
    with pytest.raises(LifecycleError, match="pending work"):
        transition_unit(unit, "learned")
    update_checkpoint(
        unit,
        completed=["Lifecycle implementation and verification"],
        pending=[],
        blocked_by=[],
        next_action="Unit complete",
    )
    criteria["visibility"] = "public"
    write_json_atomic(criteria_path, criteria)
    with pytest.raises(LifecycleError, match="evaluation criteria must be evaluation-only"):
        transition_unit(unit, "learned")
    criteria["visibility"] = "evaluation-only"
    write_json_atomic(criteria_path, criteria)
    result = transition_unit(unit, "learned")

    assert result["from"] == "operating"
    assert result["to"] == "learned"
    assert result["phase"] == "operations"

    decisions = json.loads((unit / "decisions.json").read_text(encoding="utf-8"))
    assert len(decisions["decisions"]) == 4
    assert all(DECISION_REQUIRED_FIELDS <= decision.keys() for decision in decisions["decisions"])


def test_status_exposes_the_human_gate_for_the_next_transition(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    transition_unit(unit, "inception")
    transition_unit(unit, "awaiting-inception-decision")

    pending = verify_unit(unit)["human_gate"]
    assert pending == {
        "next_transition": "construction",
        "gate": "inception",
        "decision": "required",
        "blocks_next_transition": True,
        "confirmation_required": True,
        "confirmation_channel": "interactive-human-or-authenticated-external-approval",
        "core_identity_verification": "not-performed-by-core",
    }

    approve(unit, "inception")
    approved = verify_unit(unit)["human_gate"]
    assert approved["gate"] == "inception"
    assert approved["decision"] == "approved"
    assert approved["blocks_next_transition"] is False

    transition_unit(unit, "construction")
    architecture = verify_unit(unit)["human_gate"]
    assert architecture["gate"] == "architecture"
    assert architecture["confirmation_required"] is True


def test_operating_verify_rejects_a_tampered_release_evidence_binding(
    tmp_path: Path,
) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    approve(unit, "architecture")
    transition_unit(unit, "validation")
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
    decision["attestation"]["reported_actor"] = "different-actor"
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
    transition_unit(unit, "validation")
    transition_unit(unit, "awaiting-release-decision")
    passing_evidence(unit)
    approve(unit, "release")

    authorization = authorize_action(
        unit,
        action="read",
        target="src/after-verification.py",
    )

    assert authorization["allowed"] is True
    with pytest.raises(LifecycleError, match="passing verification Evidence"):
        transition_unit(unit, "releasing")
    assert any("stale" in issue for issue in verify_unit(unit)["issues"])

    passing_evidence(unit)
    with pytest.raises(IntegrityError, match="Release Decision"):
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
        summary="승인 전에 범위를 수정해야 한다.",
        rationale=["현재 범위는 이 Gate에 비해 너무 넓다."],
        alternatives=[
            {"option": "지금 승인한다.", "reason": "범위가 제한되지 않아 기각했다."}
        ],
        tradeoffs=["승인을 연기하면 Construction 진입도 늦어진다."],
        risks=["그대로 진행하면 범위가 모호하게 남는다."],
        references=["tests/test_decision_lifecycle.py"],
        decided_by="human-reviewer",
    )

    with pytest.raises(LifecycleError, match="approved inception Decision"):
        transition_unit(unit, "construction")

    approve(unit, "inception")
    transition_unit(unit, "construction")


def test_invalid_skip_transition_is_rejected(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)

    with pytest.raises(LifecycleError, match="invalid lifecycle transition"):
        transition_unit(unit, "construction")


@pytest.mark.parametrize("gate", ["architecture", "release", "operation", "knowledge"])
def test_decisions_cannot_be_preapproved_before_their_gate(
    tmp_path: Path,
    gate: str,
) -> None:
    unit = make_unit(tmp_path)

    with pytest.raises(LifecycleError, match=f"{gate} Decision cannot be recorded"):
        approve(unit, gate)


def test_evidence_requires_a_current_test_authorization(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    with pytest.raises(LifecycleError, match="after Construction"):
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

    with pytest.raises(EvidenceError, match="current test authorization"):
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

    with pytest.raises(AuthorizationError, match="Action ledger blocks Evidence") as error:
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
        "observed_at": datetime.now(timezone.utc).isoformat(),
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

    with pytest.raises(EvidenceError, match="latest authorized test actions"):
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
                "observed_at": datetime.now(timezone.utc).isoformat(),
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

    with pytest.raises(EvidenceError, match="output_digest"):
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


@pytest.mark.parametrize("invalid_exit_code", [None, "0", False])
def test_output_evidence_rejects_non_integer_exit_codes(
    tmp_path: Path,
    invalid_exit_code: object,
) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)

    with pytest.raises(EvidenceError, match="exit_code must be an integer"):
        record_evidence(
            unit,
            passed=True,
            scope="invalid exit code",
            recorded_by="test-validator",
            commands=[
                {
                    "command": "pytest -q",
                    "exit_code": invalid_exit_code,
                    "output": "caller-observed output",
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "authorization_id": authorization_id,
                }
            ],
        )


def test_output_evidence_rejects_a_missing_exit_code(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)

    with pytest.raises(EvidenceError, match="missing fields: exit_code"):
        record_evidence(
            unit,
            passed=True,
            scope="missing exit code",
            recorded_by="test-validator",
            commands=[
                {
                    "command": "pytest -q",
                    "output": "caller-observed output",
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "authorization_id": authorization_id,
                }
            ],
        )


def test_evidence_rejects_invalid_observation_timestamp(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)

    with pytest.raises(EvidenceError, match="observed_at"):
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


@pytest.mark.parametrize(
    ("observed_at", "message"),
    [
        ("2000-01-01T00:00:00+00:00", "precedes its authorization"),
        ("2099-01-01T00:00:00+00:00", "after Evidence recorded_at"),
    ],
)
def test_evidence_rejects_observations_outside_the_authorized_time_window(
    tmp_path: Path,
    observed_at: str,
    message: str,
) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)

    with pytest.raises(EvidenceError, match=message):
        record_evidence(
            unit,
            passed=True,
            scope="invalid evidence chronology",
            recorded_by="test-validator",
            commands=[
                {
                    "command": "pytest -q",
                    "exit_code": 0,
                    "output_digest": "a" * 64,
                    "observed_at": observed_at,
                    "authorization_id": authorization_id,
                }
            ],
        )


def test_command_evidence_digest_is_derived_from_output(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)
    command = build_command_evidence(
        "pytest -q",
        0,
        "all tests passed",
        datetime.now(timezone.utc).isoformat(),
    )
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
    assert result["evidence"]["attestation"]["output_digest_verification"] == "core-derived"


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
                "name": "validation",
                "depth": "standard",
                "allowed_actions": ["read", "test"],
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
    transition_unit(unit, "validation")
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
    with pytest.raises(LifecycleError, match="passing verification Evidence"):
        transition_unit(unit, "operating")
    assert json.loads((unit / "unit.json").read_text(encoding="utf-8"))["status"] == "releasing"


def test_decision_packet_requires_rationale_and_explained_alternatives(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    with pytest.raises(IntegrityError, match="Decision Packet rejected"):
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

    with pytest.raises(IntegrityError, match="needs reason"):
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

    with pytest.raises(LifecycleError, match="approved inception Decision"):
        transition_unit(second, "construction")
