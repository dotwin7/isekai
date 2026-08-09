from __future__ import annotations

import json
from pathlib import Path

import pytest

from isekai.session import update_checkpoint
from isekai.workflow import (
    record_decision,
    record_unit_amendment,
    transition_unit,
    verify_unit,
)
from isekai.workflow.errors import IntegrityError, LifecycleError

from test_decision_lifecycle import (
    approve,
    complete_acceptance,
    make_unit,
    passing_evidence,
    start_construction,
)


def approve_architecture_amendment(unit: Path, amendment_id: str) -> None:
    record_decision(
        unit,
        gate="architecture",
        outcome="approved",
        summary="변경 요청을 반영한 Architecture를 승인한다.",
        rationale=["변경된 구현과 문서를 다시 검토했다."],
        alternatives=[],
        tradeoffs=["재검증을 위해 완료 시점이 늦어졌다."],
        risks=["이전 Architecture 승인은 변경된 결과를 포함하지 않는다."],
        references=[
            amendment_id,
            "architecture.md",
            "implementation-guide.md",
        ],
        decided_by="human-reviewer",
    )


def test_active_unit_amendment_rewinds_and_requires_changed_docs_and_decision(
    tmp_path: Path,
) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    approve(unit, "architecture")
    transition_unit(unit, "validation")

    amendment = record_unit_amendment(
        unit,
        request="결과에 정렬 안정성 보장을 추가한다.",
        reason="사용자가 완료 전 추가 동작을 요청했다.",
        affected_artifacts=["architecture.md", "implementation-guide.md"],
        requested_by="human-reviewer",
    )

    status = verify_unit(unit)
    decisions = json.loads((unit / "decisions.json").read_text(encoding="utf-8"))
    evidence = json.loads(
        (unit / "evidence/verification.json").read_text(encoding="utf-8")
    )
    assert amendment["from_status"] == "validation"
    assert amendment["status"] == "construction"
    assert status["amendments"]["active_unit"] is True
    assert status["amendments"]["pending_count"] == 1
    assert status["valid"] is False
    assert decisions["decisions"][-1]["gate"] == "amendment"
    assert decisions["decisions"][-1]["outcome"] == "approved"
    assert evidence["passed"] is False
    with pytest.raises(LifecycleError, match="unresolved Unit amendments"):
        transition_unit(unit, "validation")
    with pytest.raises(IntegrityError, match="has not changed"):
        approve_architecture_amendment(unit, amendment["amendment_id"])

    for relative in ("architecture.md", "implementation-guide.md"):
        path = unit / relative
        path.write_text(
            path.read_text(encoding="utf-8") + "\n정렬 안정성 보장을 반영한다.\n",
            encoding="utf-8",
        )
    update_checkpoint(
        unit,
        completed=["정렬 안정성 변경과 문서 반영"],
        pending=["변경된 Architecture 승인"],
        blocked_by=[],
        next_action="변경된 Architecture Decision을 기록한다.",
    )
    approve_architecture_amendment(unit, amendment["amendment_id"])

    assert verify_unit(unit)["amendments"]["pending_count"] == 0
    assert transition_unit(unit, "validation")["to"] == "validation"


def test_inception_amendment_stays_in_same_unit_and_requires_fresh_approval(
    tmp_path: Path,
) -> None:
    unit = make_unit(tmp_path)
    start_construction(unit)
    unit_id = json.loads((unit / "unit.json").read_text(encoding="utf-8"))["id"]

    amendment = record_unit_amendment(
        unit,
        request="빈 입력 처리 요구사항과 인수 조건을 추가한다.",
        reason="사용자가 같은 Unit에서 요구사항을 확장했다.",
        affected_artifacts=["requirements.md", "acceptance.md"],
        requested_by="human-reviewer",
    )

    assert amendment["required_gate"] == "inception"
    assert amendment["status"] == "inception"
    assert json.loads((unit / "unit.json").read_text(encoding="utf-8"))["id"] == unit_id
    requirements = unit / "requirements.md"
    requirements.write_text(
        requirements.read_text(encoding="utf-8") + "\n- 빈 입력을 명시적으로 처리한다.\n",
        encoding="utf-8",
    )
    acceptance = unit / "acceptance.md"
    acceptance.write_text(
        acceptance.read_text(encoding="utf-8")
        + "\n- [ ] 빈 입력이 결정적인 결과를 반환한다.\n",
        encoding="utf-8",
    )
    transition_unit(unit, "awaiting-inception-decision")
    record_decision(
        unit,
        gate="inception",
        outcome="approved",
        summary="확장된 요구사항과 기존 Envelope를 승인한다.",
        rationale=["변경이 기존 실행 범위 안에 있다."],
        alternatives=[],
        tradeoffs=["추가 검증이 필요하다."],
        risks=["빈 입력 회귀 가능성을 검증해야 한다."],
        references=[
            amendment["amendment_id"],
            "requirements.md",
            "acceptance.md",
            "execution-envelope.json",
        ],
        decided_by="human-reviewer",
    )

    assert transition_unit(unit, "construction")["to"] == "construction"
    assert verify_unit(unit)["amendments"]["pending_count"] == 0


def test_unit_remains_active_for_changes_until_final_operation_approval(
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
    unit_id = json.loads((unit / "unit.json").read_text(encoding="utf-8"))["id"]

    amendment = record_unit_amendment(
        unit,
        request="최종 완료 전에 운영 확인 절차를 추가한다.",
        reason="사용자가 같은 Unit의 운영 절차를 보완했다.",
        affected_artifacts=["operations.md"],
        requested_by="human-reviewer",
    )

    assert amendment["status"] == "operating"
    assert json.loads((unit / "unit.json").read_text(encoding="utf-8"))["id"] == unit_id
    assert verify_unit(unit)["amendments"]["active_unit"] is True
    with pytest.raises(IntegrityError, match="has not changed"):
        record_decision(
            unit,
            gate="operation",
            outcome="approved",
            summary="변경된 운영 결과를 승인한다.",
            rationale=["최종 운영 절차를 검토했다."],
            alternatives=[],
            tradeoffs=["운영 확인 단계가 추가되었다."],
            risks=["확인 절차를 누락하면 완료 판단이 부정확하다."],
            references=[amendment["amendment_id"], "operations.md"],
            decided_by="human-reviewer",
        )

    operations = unit / "operations.md"
    operations.write_text(
        operations.read_text(encoding="utf-8") + "\n- 최종 운영 확인을 수행한다.\n",
        encoding="utf-8",
    )
    passing_evidence(unit)
    record_decision(
        unit,
        gate="operation",
        outcome="approved",
        summary="변경된 운영 결과를 승인한다.",
        rationale=["추가된 운영 절차와 검증 결과를 검토했다."],
        alternatives=[],
        tradeoffs=["운영 확인 단계가 추가되었다."],
        risks=["확인 절차를 누락하면 완료 판단이 부정확하다."],
        references=[amendment["amendment_id"], "operations.md"],
        decided_by="human-reviewer",
    )
    update_checkpoint(
        unit,
        completed=["운영 절차 변경, 검증, 최종 승인"],
        pending=[],
        blocked_by=[],
        next_action="Unit을 learned로 전환한다.",
    )

    assert transition_unit(unit, "learned")["to"] == "learned"
    assert verify_unit(unit)["amendments"]["active_unit"] is False


def test_learned_unit_requires_a_new_unit_instead_of_amendment(tmp_path: Path) -> None:
    unit = make_unit(tmp_path)
    unit_value = json.loads((unit / "unit.json").read_text(encoding="utf-8"))
    unit_value["status"] = "learned"
    unit_value["phase"] = "operations"
    (unit / "unit.json").write_text(
        json.dumps(unit_value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(LifecycleError, match="start a new Unit"):
        record_unit_amendment(
            unit,
            request="완료 뒤 별도 기능을 추가한다.",
            reason="최종 완료 이후 요청이다.",
            affected_artifacts=["requirements.md"],
            requested_by="human-reviewer",
        )
