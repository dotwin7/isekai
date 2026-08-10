from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from isekai.support.jsonio import write_json_atomic
from isekai.workflow.errors import (
    AuthorizationError,
    EvidenceError,
    IntegrityError,
    LifecycleError,
)
from isekai.workflow import (
    DECISION_REQUIRED_FIELDS,
    initialize_unit,
    propose_execution_envelope,
    record_decision,
    record_evidence,
    transition_unit,
    verify_unit,
)
from isekai.catalog.ai_dlc.unit.execution import _issue_action_grant as authorize_action
from isekai.catalog.ai_dlc.unit.proof_runtime import (
    proof_command_text,
    proof_output_digest,
)
from isekai.workflow.session import update_checkpoint

from test_core_workflow import make_project, materialize_unit_artifacts


def make_unit(tmp_path: Path) -> Path:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Decision Lifecycle", project.parent / "units")
    materialize_unit_artifacts(unit)
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
    materialize_unit_artifacts(unit)
    transition_unit(unit, "inception")
    transition_unit(unit, "awaiting-inception-decision")
    approve(unit, "inception")
    transition_unit(unit, "construction")


def authorize_test(
    unit: Path,
    *,
    target: str = "tests/test_decision_lifecycle.py",
    exit_code: int = 0,
) -> str:
    authorization = authorize_action(
        unit,
        action="test",
        target=target,
    )
    assert authorization["allowed"] is True
    ledger_path = unit / "execution-authorizations.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    grant = ledger["grants"][-1]
    assert grant["id"] == authorization["authorization_id"]
    completed_at = datetime.now(timezone.utc).isoformat()
    execution = {
        "type": "core-proof",
        "status": "completed",
        "workspace": "disposable-copy",
        "sandbox_provider": "test-double",
        "filesystem_isolation": "source-and-user-data-read-denied-write-confined",
        "network_isolation": "denied",
        "process_isolation": "process-group-cleanup",
        "resource_limits": {
            "cpu_seconds": 305,
            "file_size_bytes": 256 * 1024 * 1024,
            "open_files": 256,
            "processes": 512,
            "core_dump_bytes": 0,
        },
        "environment": "core-allowlisted",
        "command": ["pytest", "-q"],
        "exit_code": exit_code,
        "timed_out": False,
        "stdout_digest": "sha256:" + "a" * 64,
        "stderr_digest": "sha256:" + "0" * 64,
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "stdout_truncated": False,
        "stderr_truncated": False,
        "output_capture_limit_bytes": 8 * 1024 * 1024,
        "output_limit_exceeded": False,
        "completed_at": completed_at,
    }
    execution["evidence_command"] = proof_command_text(execution["command"])
    execution["evidence_output_digest"] = proof_output_digest(execution)
    grant["execution"] = execution
    write_json_atomic(ledger_path, ledger)
    return str(authorization["authorization_id"])


def complete_acceptance(unit: Path) -> None:
    (unit / "acceptance.md").write_text(
        "# 인수 조건\n\n- [x] 승인된 테스트 동작이 통과한다.\n",
        encoding="utf-8",
    )


def test_inception_transition_rejects_unmaterialized_templates(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Template Must Not Advance", project.parent / "units")

    with pytest.raises(
        LifecycleError,
        match="requires materialized Unit artifacts",
    ):
        transition_unit(unit, "inception")

    unit_value = json.loads((unit / "unit.json").read_text(encoding="utf-8"))
    assert unit_value["status"] == "proposed"
    assert any(
        "plan.md still contains the ISEKAI placeholder marker" in issue
        for issue in verify_unit(unit)["issues"]
    )


@pytest.mark.parametrize("marker", ["[]", "[xx]"])
def test_inception_rejects_malformed_acceptance_checkboxes(
    tmp_path: Path,
    marker: str,
) -> None:
    unit = make_unit(tmp_path)
    (unit / "acceptance.md").write_text(
        f"# 인수 조건\n\n- {marker} 잘못된 체크박스는 기준이 아니다.\n",
        encoding="utf-8",
    )

    with pytest.raises(LifecycleError, match="checkable criterion"):
        transition_unit(unit, "inception")


@pytest.mark.parametrize("criterion", ["- [ ]", "- [ ]   ", "- [x]"])
def test_inception_rejects_acceptance_checkboxes_without_criterion_text(
    tmp_path: Path,
    criterion: str,
) -> None:
    unit = make_unit(tmp_path)
    (unit / "acceptance.md").write_text(
        f"# 인수 조건\n\n{criterion}\n",
        encoding="utf-8",
    )

    with pytest.raises(LifecycleError, match="checkable criterion"):
        transition_unit(unit, "inception")


def test_inception_decision_binds_the_materialized_plan(
    tmp_path: Path,
) -> None:
    unit = make_unit(tmp_path)
    transition_unit(unit, "inception")
    transition_unit(unit, "awaiting-inception-decision")
    approve(unit, "inception")
    decisions = json.loads((unit / "decisions.json").read_text(encoding="utf-8"))
    snapshot = decisions["decisions"][-1]["artifact_snapshot"]
    assert [item["reference"] for item in snapshot["artifacts"]] == [
        "intent.md",
        "requirements.md",
        "plan.md",
        "acceptance.md",
    ]

    plan = unit / "plan.md"
    plan.write_text(
        plan.read_text(encoding="utf-8") + "\n승인 뒤 추가된 구현 단계다.\n",
        encoding="utf-8",
    )

    with pytest.raises(IntegrityError, match="plan.md changed after"):
        transition_unit(unit, "construction")


def test_architecture_decision_binds_construction_documents(
    tmp_path: Path,
) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    approve(unit, "architecture")
    architecture = unit / "architecture.md"
    architecture.write_text(
        architecture.read_text(encoding="utf-8") + "\n승인 뒤 변경된 구조다.\n",
        encoding="utf-8",
    )

    with pytest.raises(IntegrityError, match="architecture.md changed after"):
        transition_unit(unit, "validation")


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
        commands=[{"authorization_id": authorization_id}],
    )
    update_checkpoint(
        unit,
        completed=["현재 단계 검증과 Evidence 기록"],
        pending=["다음 lifecycle Decision"],
        blocked_by=[],
        next_action="현재 검증 결과를 검토하고 다음 Decision을 기록한다.",
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
            commands=[{"authorization_id": authorization_id}],
        )

    assert list(external.iterdir()) == []


def test_verify_audits_every_historical_evidence_record(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)
    record_evidence(
        unit,
        passed=True,
        scope="valid current Evidence",
        recorded_by="test-validator",
        commands=[{"authorization_id": authorization_id}],
    )
    write_json_atomic(
        unit / "evidence/records/EVD-20260810000000000000.json",
        {"this": "is not verification Evidence"},
    )

    verification = verify_unit(unit)

    assert verification["valid"] is False
    assert any(
        "historical verification evidence" in issue
        or "verification evidence missing fields" in issue
        for issue in verification["issues"]
    )


def test_verify_rejects_a_tampered_historical_evidence_digest(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)
    result = record_evidence(
        unit,
        passed=False,
        scope="historical failed Evidence",
        recorded_by="test-validator",
        commands=[{"authorization_id": authorization_id}],
    )
    record_path = Path(result["record_path"])
    historical = json.loads(record_path.read_text())
    historical["scope"] = "tampered historical scope"
    write_json_atomic(record_path, historical)

    verification = verify_unit(unit)

    assert "verification evidence record_digest does not match its record" in (
        verification["issues"]
    )


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
    complete_acceptance(unit)
    architecture = unit / "architecture.md"
    architecture_content = architecture.read_text(encoding="utf-8")
    architecture.unlink()
    with pytest.raises(LifecycleError, match="missing Unit file: architecture.md"):
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
        "latest_decision_id": None,
        "review_round": 1,
        "revision_requested": False,
        "confirmation_required": True,
    }

    approve(unit, "inception")
    approved = verify_unit(unit)["human_gate"]
    assert approved["gate"] == "inception"
    assert approved["decision"] == "approved"
    assert approved["confirmation_required"] is False

    transition_unit(unit, "construction")
    architecture = verify_unit(unit)["human_gate"]
    assert architecture["gate"] == "architecture"
    assert architecture["confirmation_required"] is True


@pytest.mark.parametrize(
    ("content", "issue"),
    [
        (
            "# 인수 조건\n\n* [ ] 대체 Markdown bullet이 아직 열려 있다.\n",
            "acceptance criteria remain unchecked",
        ),
        ("# 인수 조건\n", "acceptance criteria are missing"),
    ],
)
def test_verify_reports_incomplete_acceptance_markdown(
    tmp_path: Path,
    content: str,
    issue: str,
) -> None:
    unit = make_unit(tmp_path)
    (unit / "acceptance.md").write_text(content, encoding="utf-8")

    assert issue in verify_unit(unit)["issues"]


def test_human_gate_reopens_after_revision_feedback(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    first_review = verify_unit(unit)["human_gate"]
    assert first_review["decision"] == "required"
    assert first_review["review_round"] == 1

    rejected_result = record_decision(
        unit,
        gate="architecture",
        outcome="rejected",
        summary="추가 요구사항을 반영한 뒤 다시 검수한다.",
        rationale=["사용자가 현재 결과에 수정 사항을 요청했다."],
        alternatives=[
            {
                "option": "기존 승인을 유지한다.",
                "reason": "수정된 결과를 포함하지 않아 기각했다.",
            }
        ],
        tradeoffs=["수정과 재검증으로 완료 시점이 늦어진다."],
        risks=["이전 승인을 재사용하면 새 변경이 검수되지 않는다."],
        references=["requirements.md", "architecture.md"],
        decided_by="human-reviewer",
    )

    reopened = verify_unit(unit)["human_gate"]
    assert reopened["decision"] == "rejected"
    assert reopened["latest_decision_id"] == rejected_result["decision_id"]
    assert reopened["review_round"] == 2
    assert reopened["revision_requested"] is True
    assert reopened["confirmation_required"] is True
    with pytest.raises(LifecycleError, match="approved architecture Decision"):
        transition_unit(unit, "validation")

    approve(unit, "architecture")
    reapproved = verify_unit(unit)["human_gate"]
    assert reapproved["decision"] == "approved"
    assert reapproved["review_round"] == 2
    assert reapproved["revision_requested"] is False

    second_rejection_result = record_decision(
        unit,
        gate="architecture",
        outcome="rejected",
        summary="두 번째 보완 요청도 반영한 뒤 다시 검수한다.",
        rationale=["사용자가 승인 뒤 추가 변경을 요청했다."],
        alternatives=[],
        tradeoffs=["검수 라운드가 한 번 더 필요하다."],
        risks=["두 번째 변경도 별도 승인 없이 진행하면 안 된다."],
        references=["requirements.md"],
        decided_by="human-reviewer",
    )
    reopened_again = verify_unit(unit)["human_gate"]
    assert reopened_again["decision"] == "rejected"
    assert reopened_again["latest_decision_id"] == second_rejection_result["decision_id"]
    assert reopened_again["review_round"] == 3
    assert reopened_again["revision_requested"] is True

    approve(unit, "architecture")
    assert verify_unit(unit)["human_gate"]["review_round"] == 3
    assert transition_unit(unit, "validation")["to"] == "validation"


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
    with pytest.raises(IntegrityError, match="release Decision"):
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


def test_evidence_requires_a_core_proof_execution_receipt(
    tmp_path: Path,
) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization = authorize_action(
        unit,
        action="test",
        target="tests/test_decision_lifecycle.py",
    )

    with pytest.raises(EvidenceError, match="Core proof execution receipt"):
        record_evidence(
            unit,
            passed=True,
            scope="unexecuted test grant",
            recorded_by="test-validator",
            commands=[
                {
                    "command": "pytest -q",
                    "exit_code": 0,
                    "output_digest": "a" * 64,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "authorization_id": authorization["authorization_id"],
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
    command = {"authorization_id": authorization_id}
    record_evidence(
        unit,
        passed=True,
        scope="initial verification",
        recorded_by="test-validator",
        commands=[command],
    )
    update_checkpoint(
        unit,
        completed=["초기 검증 Evidence 기록"],
        pending=["검증 이후 변경"],
        blocked_by=[],
        next_action="검증 이후 변경을 수행한다.",
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
    authorization_id = authorize_test(unit, exit_code=1)
    result = record_evidence(
        unit,
        passed=False,
        scope="intentional failure case",
        recorded_by="test-validator",
        commands=[{"authorization_id": authorization_id}],
    )

    assert result["passed"] is False
    verification = verify_unit(unit)
    assert "verification evidence is not passing" in verification["issues"]


def test_evidence_command_is_derived_from_the_core_proof_receipt(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)

    record_evidence(
        unit,
        passed=True,
        scope="receipt-derived Evidence",
        recorded_by="test-validator",
        commands=[{"authorization_id": authorization_id}],
    )

    ledger = json.loads((unit / "execution-authorizations.json").read_text())
    execution = ledger["grants"][-1]["execution"]
    evidence = json.loads((unit / "evidence/verification.json").read_text())
    command = evidence["commands"][0]
    assert command == {
        "authorization_id": authorization_id,
        "command": execution["evidence_command"],
        "exit_code": execution["exit_code"],
        "output_digest": execution["evidence_output_digest"],
        "observed_at": execution["completed_at"],
    }
    assert evidence["schema_version"] == "1.1.0"
    assert evidence["record_digest"].startswith("sha256:")
    assert evidence["attestation"]["output_digest_verification"] == (
        "core-receipt-derived"
    )
    assert evidence["attestation"]["execution_verification"] == (
        "core-proof-receipt"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command", "forged security scan"),
        ("exit_code", 99),
        ("output_digest", "f" * 64),
        ("observed_at", "2099-01-01T00:00:00+00:00"),
    ],
)
def test_evidence_rejects_fields_that_disagree_with_the_core_proof_receipt(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)

    with pytest.raises(IntegrityError, match=f"{field} does not match"):
        record_evidence(
            unit,
            passed=True,
            scope="forged Evidence field",
            recorded_by="test-validator",
            commands=[{"authorization_id": authorization_id, field: value}],
        )


def test_evidence_rejects_caller_output_for_a_core_proof(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)

    with pytest.raises(EvidenceError, match="output is derived"):
        record_evidence(
            unit,
            passed=True,
            scope="caller output",
            recorded_by="test-validator",
            commands=[
                {"authorization_id": authorization_id, "output": "forged output"}
            ],
        )


def test_passing_evidence_rejects_an_incomplete_core_proof(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)
    ledger_path = unit / "execution-authorizations.json"
    ledger = json.loads(ledger_path.read_text())
    execution = ledger["grants"][-1]["execution"]
    execution["status"] = "output-limit-exceeded"
    execution["output_limit_exceeded"] = True
    write_json_atomic(ledger_path, ledger)

    with pytest.raises(EvidenceError, match="cannot pass with an incomplete"):
        record_evidence(
            unit,
            passed=True,
            scope="incomplete proof",
            recorded_by="test-validator",
            commands=[{"authorization_id": authorization_id}],
        )


def test_evidence_rejects_a_tampered_proof_output_binding(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    authorization_id = authorize_test(unit)
    ledger_path = unit / "execution-authorizations.json"
    ledger = json.loads(ledger_path.read_text())
    ledger["grants"][-1]["execution"]["evidence_output_digest"] = "f" * 64
    write_json_atomic(ledger_path, ledger)

    with pytest.raises(EvidenceError, match="invalid Core proof Evidence output binding"):
        record_evidence(
            unit,
            passed=True,
            scope="tampered receipt binding",
            recorded_by="test-validator",
            commands=[{"authorization_id": authorization_id}],
        )


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
    with pytest.raises(LifecycleError, match="current checkpoint"):
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
        materialize_unit_artifacts(unit)
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
