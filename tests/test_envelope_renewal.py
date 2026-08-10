from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from isekai.workflow import (
    UNIT_LOCK_NAME,
    EXECUTION_ENVELOPE_DEFAULT_HOURS,
    EXECUTION_ENVELOPE_MAX_HOURS,
    approve_execution_envelope,
    authorize_action,
    propose_execution_envelope,
    record_decision,
    transition_unit,
    verify_unit,
)
from isekai.workflow.errors import AuthorizationError, IntegrityError
from isekai.workflow.session import update_checkpoint

from test_core_workflow import make_project
from test_decision_lifecycle import (
    approve,
    complete_acceptance,
    make_unit,
    passing_evidence,
)
from test_execution_envelope import (
    approve_inception,
    envelope_stages,
    make_enveloped_unit,
)


def expire_envelope(unit: Path) -> None:
    """Rewrite the Envelope as if it had been approved before its window closed."""
    from isekai.workflow import (
        _decision_record_digest,
        _execution_envelope_approval_digest,
    )

    path = unit / "execution-envelope.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    past = datetime.now(timezone.utc) - timedelta(days=30)
    envelope["proposed_at"] = past.isoformat()
    envelope["expires_at"] = (past + timedelta(hours=1)).isoformat()
    envelope["approval_digest"] = _execution_envelope_approval_digest(envelope)

    decisions_path = unit / "decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    previous_digest = None
    for decision in decisions["decisions"]:
        subject = decision.get("approval_subject")
        if isinstance(subject, dict):
            subject["digest"] = envelope["approval_digest"]
        decision["previous_decision_digest"] = previous_digest
        decision["decision_digest"] = _decision_record_digest(decision)
        previous_digest = decision["decision_digest"]
    latest_inception = next(
        decision
        for decision in reversed(decisions["decisions"])
        if decision.get("gate") == "inception"
    )
    envelope["approval_decision_digest"] = latest_inception["decision_digest"]
    path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    decisions_path.write_text(json.dumps(decisions, indent=2) + "\n", encoding="utf-8")

    ledger_path = unit / "execution-authorizations.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["approval_digest"] = envelope["approval_digest"]
    for grant in ledger["grants"]:
        grant["envelope_digest"] = envelope["approval_digest"]
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")


def renew(unit: Path, *, max_iterations: int = 3) -> None:
    update_checkpoint(
        unit,
        completed=["현재 Envelope의 승인 작업 기록"],
        pending=["교체 Envelope 승인"],
        blocked_by=[],
        next_action="교체 Envelope를 제안하고 승인한다.",
    )
    propose_execution_envelope(
        unit,
        scope=["src/**", "tests/**"],
        stages=envelope_stages(),
        allowed_actions=["read", "edit", "test"],
        forbidden_actions=["remote", "deploy", "credential-access"],
        max_iterations=max_iterations,
        proposed_by="planner-agent",
    )
    record_decision(
        unit,
        gate="inception",
        outcome="approved",
        summary="다음 반복을 위해 Construction Envelope를 갱신한다.",
        rationale=["남은 작업에도 범위가 제한된 편집 권한이 필요하다."],
        alternatives=[
            {"option": "Unit을 종료한다.", "reason": "남은 작업이 있어 기각했다."}
        ],
        tradeoffs=["새 승인 기간은 반복 예산을 다시 시작한다."],
        risks=["더 넓은 범위에는 별도 검토가 필요하다."],
        references=["requirements.md", "execution-envelope.json"],
        decided_by="human-reviewer",
    )
    approve_execution_envelope(unit)


def test_expired_envelope_blocks_actions_but_keeps_the_unit_verifiable(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    expire_envelope(unit)

    blocked = authorize_action(unit, action="edit", target="src/main.py")
    result = verify_unit(unit)

    assert blocked["allowed"] is False
    assert "expired" in blocked["reason"]
    # A lapsed approval window must not permanently invalidate the Unit record.
    assert not [issue for issue in result["issues"] if "Envelope" in issue]


def test_expired_envelope_is_renewed_by_a_new_decision(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    expire_envelope(unit)
    assert authorize_action(unit, action="edit", target="src/main.py")["allowed"] is False

    renew(unit)

    allowed = authorize_action(unit, action="edit", target="src/main.py")
    assert allowed["allowed"] is True
    assert allowed["iteration"] == 1
    assert verify_unit(unit)["issues"] == [
        issue
        for issue in verify_unit(unit)["issues"]
        if "Envelope" not in issue
    ]


def test_exhausted_iteration_budget_is_recoverable_by_renewal(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    from isekai.workflow import initialize_unit

    unit = initialize_unit(project, "Budget Renewal", project.parent / "units")
    propose_execution_envelope(
        unit,
        scope=["src/**", "tests/**"],
        stages=envelope_stages(),
        allowed_actions=["read", "edit", "test"],
        forbidden_actions=["remote"],
        max_iterations=1,
        proposed_by="planner-agent",
    )
    approve_inception(unit)

    assert authorize_action(unit, action="edit", target="src/first.py")["allowed"] is True
    exhausted = authorize_action(unit, action="edit", target="src/second.py")
    assert exhausted["allowed"] is False
    assert "exhausted" in exhausted["reason"]
    previous_envelope = json.loads(
        (unit / "execution-envelope.json").read_text(encoding="utf-8")
    )
    previous_ledger = json.loads(
        (unit / "execution-authorizations.json").read_text(encoding="utf-8")
    )

    renew(unit, max_iterations=2)

    archived = json.loads(
        (
            unit
            / "execution-authorization-records"
            / f"{previous_envelope['id']}.json"
        ).read_text(encoding="utf-8")
    )
    assert archived["envelope"] == previous_envelope
    assert archived["authorization_ledger"] == previous_ledger
    assert archived["authorization_ledger_digest"].startswith("sha256:")
    assert not any(
        "authorization record" in issue.lower()
        for issue in verify_unit(unit)["issues"]
    )
    recovered = authorize_action(unit, action="edit", target="src/second.py")
    assert recovered["allowed"] is True
    assert recovered["iteration"] == 1


def test_renewed_authorization_archive_tampering_is_detected(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    assert authorize_action(unit, action="edit", target="src/first.py")["allowed"] is True
    previous_envelope = json.loads(
        (unit / "execution-envelope.json").read_text(encoding="utf-8")
    )
    renew(unit)
    archive_path = (
        unit
        / "execution-authorization-records"
        / f"{previous_envelope['id']}.json"
    )
    archived = json.loads(archive_path.read_text(encoding="utf-8"))
    archived["authorization_ledger"]["grants"][0]["target"] = "src/tampered.py"
    archive_path.write_text(json.dumps(archived, indent=2) + "\n", encoding="utf-8")

    result = verify_unit(unit)

    assert any(
        "authorization record ledger digest" in issue.lower()
        for issue in result["issues"]
    )


def test_validation_unit_can_renew_an_expired_envelope(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    approve(unit, "architecture")
    transition_unit(unit, "validation")
    expire_envelope(unit)

    renew(unit)

    recovered = authorize_action(unit, action="test", target="tests/validation.py")
    assert recovered["allowed"] is True
    assert recovered["stage"] == "validation"


def test_operating_unit_can_renew_an_exhausted_envelope_and_finish(
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
    complete_acceptance(unit)
    transition_unit(unit, "releasing")
    transition_unit(unit, "operating")

    # The release Evidence becomes stale after Operations grants consume the
    # remaining budget. Before renewal was available here, this was terminal.
    for index in range(4):
        grant = authorize_action(
            unit,
            action="read",
            target=f"src/operations-{index}.py",
        )
        assert grant["allowed"] is True
    exhausted = authorize_action(unit, action="test", target="tests/operations.py")
    assert exhausted["allowed"] is False
    assert "exhausted" in exhausted["reason"]

    propose_execution_envelope(
        unit,
        scope=["src/**", "tests/**"],
        stages=[
            {
                "name": "operations",
                "depth": "standard",
                "allowed_actions": ["read", "edit", "test"],
            }
        ],
        allowed_actions=["read", "edit", "test"],
        forbidden_actions=["remote", "deploy", "credential-access"],
        max_iterations=2,
        proposed_by="planner-agent",
    )
    approve(unit, "inception")
    approve_execution_envelope(unit)
    passing_evidence(unit)
    approve(unit, "operation")
    update_checkpoint(
        unit,
        completed=["Operations verification"],
        pending=[],
        blocked_by=[],
        next_action="Unit complete",
    )

    assert transition_unit(unit, "learned")["to"] == "learned"


def test_releasing_unit_can_refresh_envelope_evidence_and_release_decision(
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
    complete_acceptance(unit)
    transition_unit(unit, "releasing")

    # The original Envelope has no Release stage, so the Unit needs a reviewed
    # replacement before it can produce fresh release Evidence.
    blocked = authorize_action(
        unit,
        action="test",
        target="tests/release.py",
    )
    assert blocked["allowed"] is False
    assert "stage" in blocked["reason"]

    propose_execution_envelope(
        unit,
        scope=["src/**", "tests/**"],
        stages=[
            {
                "name": "release",
                "depth": "standard",
                "allowed_actions": ["read", "edit", "test"],
            }
        ],
        allowed_actions=["read", "edit", "test"],
        forbidden_actions=["remote", "deploy", "credential-access"],
        max_iterations=2,
        proposed_by="planner-agent",
    )
    approve(unit, "inception")
    approve_execution_envelope(unit)
    passing_evidence(unit)
    approve(unit, "release")

    assert transition_unit(unit, "operating")["to"] == "operating"


def test_renewed_envelope_is_inert_until_the_new_decision_approves_it(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    assert authorize_action(unit, action="edit", target="src/main.py")["allowed"] is True

    propose_execution_envelope(
        unit,
        scope=["**"],
        stages=envelope_stages(),
        allowed_actions=["read", "edit", "test"],
        forbidden_actions=["remote"],
        max_iterations=9,
        proposed_by="planner-agent",
    )

    replaced = authorize_action(unit, action="edit", target="src/main.py")
    assert replaced["allowed"] is False
    assert "not approved" in replaced["reason"]
    # The previous Decision approved a different Envelope, so it cannot be
    # reused to activate the replacement.
    with pytest.raises(IntegrityError, match="replaced after the Inception Decision"):
        approve_execution_envelope(unit)


def test_envelope_lifetime_is_bounded_and_configurable(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    from isekai.workflow import initialize_unit

    unit = initialize_unit(project, "Envelope Lifetime", project.parent / "units")
    result = propose_execution_envelope(
        unit,
        scope=["src/**"],
        stages=envelope_stages(),
        allowed_actions=["read", "edit", "test"],
        forbidden_actions=["remote"],
        max_iterations=2,
        proposed_by="planner-agent",
        expires_in_hours=48,
    )
    proposed = json.loads((unit / "execution-envelope.json").read_text(encoding="utf-8"))
    window = datetime.fromisoformat(proposed["expires_at"]) - datetime.fromisoformat(
        proposed["proposed_at"]
    )

    assert window == timedelta(hours=48)
    assert EXECUTION_ENVELOPE_DEFAULT_HOURS == 168
    for invalid in (0, -1, EXECUTION_ENVELOPE_MAX_HOURS + 1, True):
        with pytest.raises(AuthorizationError, match="expires_in_hours"):
            propose_execution_envelope(
                unit,
                scope=["src/**"],
                stages=envelope_stages(),
                allowed_actions=["read"],
                forbidden_actions=[],
                max_iterations=1,
                proposed_by="planner-agent",
                expires_in_hours=invalid,
            )


def test_abandoned_authorization_lock_file_is_reclaimed_without_delay(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    lock = unit / UNIT_LOCK_NAME
    lock.write_text("", encoding="utf-8")

    reclaimed = authorize_action(unit, action="edit", target="src/main.py")

    assert reclaimed["allowed"] is True
    assert not lock.exists()


def test_authorization_lock_is_not_a_unit_artifact(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    (unit / UNIT_LOCK_NAME).write_text("", encoding="utf-8")

    result = verify_unit(unit)

    assert UNIT_LOCK_NAME not in json.dumps(result["missing"])
    assert result["artifact_count"] == len(
        [
            path
            for path in unit.rglob("*")
            if path.is_file() and path.name != UNIT_LOCK_NAME
        ]
    )
