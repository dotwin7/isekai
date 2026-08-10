from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

import isekai.catalog.ai_dlc.unit.execution as execution_module
import isekai.catalog.ai_dlc.unit.execution_history as execution_history_module
import isekai.catalog.ai_dlc.unit.lifecycle as lifecycle_module
from isekai.jsonio import write_json_atomic
from isekai.workflow.errors import AuthorizationError, IntegrityError
from isekai.workflow import (
    authorize_action,
    initialize_unit,
    propose_execution_envelope,
    record_decision,
    transition_unit,
    verify_unit,
)
from isekai.workflow.project import _context_receipt_id
from isekai.catalog.ai_dlc.unit.execution import _scope_pattern_matches
from isekai.session import resume_session, update_checkpoint

from test_core_workflow import make_project, materialize_unit_artifacts


def envelope_stages() -> list[dict[str, object]]:
    return [
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
    ]


def make_enveloped_unit(tmp_path: Path) -> Path:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Adaptive Envelope", project.parent / "units")
    materialize_unit_artifacts(unit)
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


def test_envelope_proposal_write_failure_restores_both_control_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Atomic Envelope", project.parent / "units")
    envelope_path = unit / "execution-envelope.json"
    ledger_path = unit / "execution-authorizations.json"
    before = (envelope_path.read_bytes(), ledger_path.read_bytes())
    original_write = execution_module._write_json
    calls = 0

    def fail_second_write(path: Path, value: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("forced authorization ledger write failure")
        original_write(path, value)

    monkeypatch.setattr(execution_module, "_write_json", fail_second_write)

    with pytest.raises(OSError, match="forced authorization ledger write failure"):
        propose_execution_envelope(
            unit,
            scope=["src/**"],
            stages=envelope_stages(),
            allowed_actions=["read", "edit", "test"],
            forbidden_actions=["remote", "deploy", "credential-access"],
            max_iterations=3,
            proposed_by="planner-agent",
        )

    assert (envelope_path.read_bytes(), ledger_path.read_bytes()) == before


def test_envelope_archive_write_failure_restores_active_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    assert authorize_action(unit, action="edit", target="src/first.py")["allowed"] is True
    envelope_path = unit / "execution-envelope.json"
    ledger_path = unit / "execution-authorizations.json"
    before = (envelope_path.read_bytes(), ledger_path.read_bytes())
    def fail_archive_write(path: Path, value: object) -> None:
        raise OSError("forced authorization archive write failure")

    monkeypatch.setattr(execution_history_module, "_write_json", fail_archive_write)

    with pytest.raises(OSError, match="forced authorization archive write failure"):
        propose_execution_envelope(
            unit,
            scope=["src/**"],
            stages=envelope_stages(),
            allowed_actions=["read", "edit", "test"],
            forbidden_actions=["remote"],
            max_iterations=3,
            proposed_by="planner-agent",
        )

    assert (envelope_path.read_bytes(), ledger_path.read_bytes()) == before
    assert not list((unit / "execution-authorization-records").glob("*.json"))


def test_envelope_accepts_an_adaptive_stage_plan_with_explicit_skip(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Adaptive Stage Plan", project.parent / "units")

    result = propose_execution_envelope(
        unit,
        scope=["src/**", "tests/**"],
        stages=[
            {
                "name": "construction",
                "disposition": "apply",
                "depth": "standard",
                "reason": "The product change needs implementation and tests.",
                "allowed_actions": ["read", "edit", "test"],
            },
            {
                "name": "release",
                "disposition": "skip",
                "depth": "light",
                "reason": "This Unit has no deployment or publication scope.",
                "allowed_actions": [],
            },
        ],
        allowed_actions=["read", "edit", "test"],
        forbidden_actions=["remote", "deploy", "credential-access"],
        max_iterations=3,
        proposed_by="planner-agent",
    )

    assert result["status"] == "proposed"
    envelope = json.loads((unit / "execution-envelope.json").read_text(encoding="utf-8"))
    assert envelope["stages"][1]["disposition"] == "skip"


@pytest.mark.parametrize(
    ("stage", "issue"),
    [
        (
            {
                "name": "construction",
                "disposition": "apply",
                "depth": "exhaustive",
                "reason": "Invalid depth.",
                "allowed_actions": ["read"],
            },
            "depth must be one of",
        ),
        (
            {
                "name": "release",
                "disposition": "skip",
                "depth": "light",
                "reason": "No release scope.",
                "allowed_actions": ["read"],
            },
            "skipped stage 0 cannot allow actions",
        ),
        (
            {
                "name": "release",
                "disposition": "skip",
                "depth": "light",
                "allowed_actions": [],
            },
            "with a disposition needs reason",
        ),
    ],
)
def test_envelope_rejects_invalid_adaptive_stage_contract(
    tmp_path: Path, stage: dict[str, object], issue: str
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Invalid Adaptive Plan", project.parent / "units")

    with pytest.raises(AuthorizationError, match=issue):
        propose_execution_envelope(
            unit,
            scope=["src/**"],
            stages=[stage],
            allowed_actions=["read"],
            forbidden_actions=["remote"],
            max_iterations=1,
            proposed_by="planner-agent",
        )


def approve_inception(unit: Path) -> None:
    materialize_unit_artifacts(unit)
    transition_unit(unit, "inception")
    transition_unit(unit, "awaiting-inception-decision")
    record_decision(
        unit,
        gate="inception",
        outcome="approved",
        summary="적응형 Construction Envelope를 승인한다.",
        rationale=["범위와 Construction 작업이 제한되어 있다."],
        alternatives=[
            {
                "option": "Envelope 없이 실행한다.",
                "reason": "작업에 명시적 제한이 필요해 기각했다.",
            }
        ],
        tradeoffs=["Envelope는 소스와 테스트 밖의 변경을 제한한다."],
        risks=["후속 작업에는 새 Envelope 제안이 필요할 수 있다."],
        references=["requirements.md", "execution-envelope.json"],
        decided_by="human-reviewer",
    )
    transition_unit(unit, "construction")


def test_validation_is_a_real_authorizable_lifecycle_phase(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    record_decision(
        unit,
        gate="architecture",
        outcome="approved",
        summary="Validation 단계 진입을 승인한다.",
        rationale=["Construction을 완료했고 제한된 테스트가 준비되었다."],
        alternatives=[],
        tradeoffs=[],
        risks=[],
        references=["architecture.md"],
        decided_by="human-reviewer",
    )

    transition = transition_unit(unit, "validation")
    authorization = authorize_action(
        unit,
        action="test",
        target="tests/test_validation.py",
    )

    assert transition["phase"] == "validation"
    assert authorization["allowed"] is True
    assert authorization["stage"] == "validation"


def test_authorized_work_requires_a_current_checkpoint_and_fresh_decision(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)

    first = authorize_action(unit, action="edit", target="src/first.py")
    resumed = resume_session(unit.parent.parent / "project.json", unit)

    assert first["allowed"] is True
    assert first["checkpoint_required"] is True
    assert resumed["resume"]["checkpoint_fresh"] is False
    assert resumed["resume"]["recovery_required"] is True
    assert "stale" in resumed["resume"]["checkpoint_issues"][0]
    with pytest.raises(IntegrityError, match="checkpoint is stale"):
        record_decision(
            unit,
            gate="architecture",
            outcome="approved",
            summary="현재 구현 구조를 승인한다.",
            rationale=["구현 진행 상태와 문서를 함께 검토했다."],
            alternatives=[],
            tradeoffs=[],
            risks=[],
            references=["architecture.md", "implementation-guide.md"],
            decided_by="human-reviewer",
        )

    update_checkpoint(
        unit,
        completed=["src/first.py 구현"],
        pending=["Architecture 검토"],
        blocked_by=[],
        next_action="Architecture Decision을 기록한다.",
    )
    record_decision(
        unit,
        gate="architecture",
        outcome="approved",
        summary="현재 구현 구조를 승인한다.",
        rationale=["구현 진행 상태와 문서를 함께 검토했다."],
        alternatives=[],
        tradeoffs=[],
        risks=[],
        references=["architecture.md", "implementation-guide.md"],
        decided_by="human-reviewer",
    )

    assert authorize_action(unit, action="edit", target="src/second.py")["allowed"]
    update_checkpoint(
        unit,
        completed=["src/first.py와 src/second.py 구현"],
        pending=["변경된 Architecture 재검토"],
        blocked_by=[],
        next_action="Architecture Decision을 다시 요청한다.",
    )
    with pytest.raises(IntegrityError, match="progress changed after"):
        transition_unit(unit, "validation")


def test_l0_project_rejects_edit_and_test_envelopes(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    manifest = json.loads(project.read_text(encoding="utf-8"))
    manifest["maximum_agent_level"] = "L0"
    project.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    unit = initialize_unit(project, "Read-only Agent Level", project.parent / "units")

    with pytest.raises(AuthorizationError, match="maximum_agent_level L0"):
        propose_execution_envelope(
            unit,
            scope=["src/**"],
            stages=[
                {
                    "name": "construction",
                    "depth": "standard",
                    "allowed_actions": ["read", "edit", "test"],
                }
            ],
            allowed_actions=["read", "edit", "test"],
            forbidden_actions=["remote"],
            max_iterations=3,
            proposed_by="planner-agent",
        )

    result = propose_execution_envelope(
        unit,
        scope=["src/**"],
        stages=[
            {
                "name": "inception",
                "depth": "light",
                "allowed_actions": ["read"],
            }
        ],
        allowed_actions=["read"],
        forbidden_actions=["remote"],
        max_iterations=1,
        proposed_by="planner-agent",
    )
    envelope = json.loads((unit / "execution-envelope.json").read_text(encoding="utf-8"))
    assert envelope["allowed_actions"] == ["read"]


def test_authorize_and_verify_enforce_the_receipt_agent_level(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    receipt_path = unit / "context-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["maximum_agent_level"] = "L0"
    receipt["receipt_id"] = _context_receipt_id(receipt)
    write_json_atomic(receipt_path, receipt)

    authorization = authorize_action(unit, action="edit", target="src/main.py")
    verification = verify_unit(unit)

    assert authorization["allowed"] is False
    assert "maximum_agent_level L0" in authorization["reason"]
    assert any(
        "maximum_agent_level L0" in issue for issue in verification["issues"]
    )


def test_construction_transition_restores_envelope_when_unit_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    transition_unit(unit, "inception")
    transition_unit(unit, "awaiting-inception-decision")
    record_decision(
        unit,
        gate="inception",
        outcome="approved",
        summary="원자적 전이 회귀 테스트 Envelope를 승인한다.",
        rationale=["제한된 테스트 범위가 Construction에 준비되었다."],
        alternatives=[],
        tradeoffs=[],
        risks=["Unit 상태 기록이 실패할 수 있다."],
        references=["execution-envelope.json"],
        decided_by="human-reviewer",
    )
    unit_path = unit / "unit.json"
    envelope_path = unit / "execution-envelope.json"
    before = (unit_path.read_bytes(), envelope_path.read_bytes())

    def fail_unit_write(path: Path, value: object) -> None:
        raise OSError("forced Unit lifecycle write failure")

    monkeypatch.setattr(lifecycle_module, "_write_json", fail_unit_write)

    with pytest.raises(OSError, match="forced Unit lifecycle write failure"):
        transition_unit(unit, "construction")

    assert (unit_path.read_bytes(), envelope_path.read_bytes()) == before


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


def test_repeated_recursive_scope_wildcards_are_bounded() -> None:
    count = 12
    pattern = "/".join(["**"] * count + ["never-match"])
    target = "/".join(["segment"] * count)

    started = time.perf_counter()
    assert _scope_pattern_matches(pattern, target) is False
    assert time.perf_counter() - started < 0.25


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

    with pytest.raises(AuthorizationError, match="Execution Envelope rejected"):
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

    with pytest.raises(AuthorizationError, match="unsupported|prohibited"):
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
        summary="이전에 승인한 실행 범위를 철회한다.",
        rationale=["승인된 범위가 더는 유효하지 않다."],
        alternatives=[
            {"option": "승인을 유지한다.", "reason": "범위가 바뀌어 기각했다."}
        ],
        tradeoffs=["새 승인을 기록할 때까지 Construction이 중단된다."],
        risks=["계속 진행하면 철회된 권한으로 실행하게 된다."],
        references=["execution-envelope.json"],
        decided_by="human-reviewer",
    )

    result = authorize_action(unit, action="edit", target="src/main.py")

    assert result["allowed"] is False
    assert any(
        marker in result["reason"]
        for marker in ("revoked", "latest", "digest chain")
    )


def test_reordering_decisions_cannot_restore_a_revoked_envelope(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    record_decision(
        unit,
        gate="inception",
        outcome="rejected",
        summary="이전에 승인한 실행 범위를 철회한다.",
        rationale=["승인된 범위가 더는 유효하지 않다."],
        alternatives=[],
        tradeoffs=[],
        risks=["계속 진행하면 철회된 권한을 사용하게 된다."],
        references=["execution-envelope.json"],
        decided_by="human-reviewer",
    )
    decisions_path = unit / "decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    decisions["decisions"][-2:] = reversed(decisions["decisions"][-2:])
    write_json_atomic(decisions_path, decisions)

    result = authorize_action(unit, action="edit", target="src/main.py")

    assert result["allowed"] is False
    assert "digest chain" in result["reason"] or "later than" in result["reason"]


def test_authorization_rechecks_approval_after_acquiring_unit_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    original_lock = execution_module.unit_lock

    @contextmanager
    def lock_after_revocation(unit_dir: Path):
        from datetime import datetime, timezone

        from isekai.workflow import _decision_record_digest

        with original_lock(unit_dir):
            path = unit_dir / "decisions.json"
            decisions = json.loads(path.read_text(encoding="utf-8"))
            revoked = dict(decisions["decisions"][-1])
            revoked.update(
                {
                    "id": "DEC-REVOKED-BEFORE-GRANT",
                    "outcome": "rejected",
                    "summary": "Approval was revoked before the grant lock was acquired.",
                    "decided_at": datetime.now(timezone.utc).isoformat(),
                    "previous_decision_digest": decisions["decisions"][-1][
                        "decision_digest"
                    ],
                }
            )
            revoked["decision_digest"] = _decision_record_digest(revoked)
            decisions["decisions"].append(revoked)
            write_json_atomic(path, decisions)
            yield

    monkeypatch.setattr(execution_module, "unit_lock", lock_after_revocation)

    result = execution_module.authorize_action(
        unit, action="edit", target="src/raced.py"
    )
    ledger = json.loads(
        (unit / "execution-authorizations.json").read_text(encoding="utf-8")
    )

    assert result["allowed"] is False
    assert "revoked" in result["reason"] or "latest" in result["reason"]
    assert ledger["grants"] == []


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
        summary="제한된 실행 범위를 승인한다.",
        rationale=["검토한 범위가 소스와 테스트로 제한되어 있다."],
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

    with pytest.raises(IntegrityError, match="replaced|changed"):
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


@pytest.mark.parametrize(
    "target",
    [r"C:\outside.txt", r"C:drive-relative.txt", r"\\server\share\outside.txt"],
)
def test_cross_platform_absolute_target_does_not_poison_authorization_ledger(
    tmp_path: Path,
    target: str,
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Portable Target", project.parent / "units")
    propose_execution_envelope(
        unit,
        scope=["**"],
        stages=envelope_stages(),
        allowed_actions=["read", "edit", "test"],
        forbidden_actions=["remote", "deploy", "credential-access"],
        max_iterations=2,
        proposed_by="planner-agent",
    )
    approve_inception(unit)

    rejected = authorize_action(unit, action="edit", target=target)
    after_rejection = json.loads(
        (unit / "execution-authorizations.json").read_text(encoding="utf-8")
    )
    accepted = authorize_action(unit, action="edit", target="src/main.py")

    assert rejected["allowed"] is False
    assert "project-relative" in rejected["reason"]
    assert after_rejection["grants"] == []
    assert accepted["allowed"] is True
    assert accepted["iteration"] == 1


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


@pytest.mark.parametrize(
    "control_relative",
    [
        "execution-authorizations.json",
        "execution-authorization-records/ENV-ARCHIVED.json",
    ],
)
def test_authorization_cannot_edit_its_own_control_ledger(
    tmp_path: Path,
    control_relative: str,
) -> None:
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
        (unit / control_relative).relative_to(project.parent)
    )

    result = authorize_action(unit, action="edit", target=ledger_target)

    assert result["allowed"] is False
    assert "control artifact" in result["reason"]
