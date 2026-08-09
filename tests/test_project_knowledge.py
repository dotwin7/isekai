from __future__ import annotations

import json
from pathlib import Path

import pytest

from isekai.jsonio import write_json_atomic
from isekai.session import build_session
from isekai.workflow import (
    initialize_unit,
    project_knowledge_status,
    promote_project_knowledge,
    propose_project_knowledge,
    record_decision,
    verify_unit,
)
from isekai.workflow.errors import IntegrityError, LifecycleError

from test_core_workflow import make_project


def _operating_unit(project: Path, title: str = "공유 지식 출처") -> Path:
    unit = initialize_unit(project, title, project.parent / "units")
    unit_record = json.loads((unit / "unit.json").read_text(encoding="utf-8"))
    unit_record["status"] = "operating"
    unit_record["phase"] = "operations"
    write_json_atomic(unit / "unit.json", unit_record)
    (unit / "architecture.md").write_text(
        "# 아키텍처\n\n모든 서비스 ID는 소문자 kebab-case를 사용한다.\n",
        encoding="utf-8",
    )
    return unit


def _entry(entry_id: str, *, replaces: str | None = None) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": entry_id,
        "kind": "convention",
        "title": "서비스 식별자 표기 규칙",
        "statement": "서비스 식별자는 소문자 kebab-case로 기록한다.",
        "scope": ["services/**"],
        "owner": "platform-team",
        "references": ["architecture.md"],
    }
    if replaces is not None:
        entry["replaces"] = replaces
    return entry


def _approve(unit: Path, reference: str) -> dict[str, object]:
    return record_decision(
        unit,
        gate="knowledge",
        outcome="approved",
        summary="재사용 가능한 프로젝트 규칙으로 승격을 승인한다.",
        rationale=["다음 Unit도 같은 식별자 규칙을 일관되게 사용해야 한다."],
        alternatives=[
            {
                "option": "현재 Unit에만 기록한다.",
                "reason": "후속 Unit의 일관성을 보장하지 못해 기각했다.",
            }
        ],
        tradeoffs=["기존 Unit은 생성 시점의 지식 버전을 계속 사용한다."],
        risks=["잘못된 공통화는 후속 Unit에 영향을 줄 수 있다."],
        references=[reference, "architecture.md"],
        decided_by="human-reviewer",
    )


def _propose(unit: Path, entry_id: str) -> dict[str, object]:
    return propose_project_knowledge(
        unit,
        entries=[_entry(entry_id)],
        proposed_by="learning-agent",
    )


def test_project_knowledge_is_pinned_per_unit_and_latest_is_used_by_future_units(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    source = _operating_unit(project)
    assert build_session(project, source)["context"]["project_knowledge"] is None

    first_candidate = _propose(source, "service-id-format-v1")
    first_reference = str(first_candidate["reference"])
    _approve(source, first_reference)
    first_release = promote_project_knowledge(
        source, candidate=first_reference
    )["release"]

    assert first_release["version"] == "0.1.0"
    assert build_session(project, source)["context"]["project_knowledge"] is None
    first_future = initialize_unit(project, "첫 번째 후속 Unit")
    first_receipt = json.loads(
        (first_future / "context-receipt.json").read_text(encoding="utf-8")
    )
    assert first_receipt["project_knowledge"]["release_digest"] == first_release[
        "release_digest"
    ]

    second_candidate = _propose(source, "api-error-format-v1")
    second_reference = str(second_candidate["reference"])
    _approve(source, second_reference)
    second_release = promote_project_knowledge(
        source, candidate=second_reference
    )["release"]

    assert second_release["version"] == "0.1.1"
    assert build_session(project, first_future)["context"]["project_knowledge"][
        "release_digest"
    ] == first_release["release_digest"]
    second_future = initialize_unit(project, "두 번째 후속 Unit")
    assert build_session(project, second_future)["context"]["project_knowledge"][
        "release_digest"
    ] == second_release["release_digest"]
    assert project_knowledge_status(project)["current_release"]["version"] == "0.1.1"


def test_approved_candidate_and_source_artifact_are_digest_bound(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    source = _operating_unit(project)
    proposed = _propose(source, "service-id-format-v1")
    reference = str(proposed["reference"])
    _approve(source, reference)

    candidate_path = project.parent / reference
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["entries"][0]["statement"] = "승인 뒤 몰래 바꾼 규칙"
    write_json_atomic(candidate_path, candidate)

    with pytest.raises(IntegrityError, match="candidate digest"):
        promote_project_knowledge(source, candidate=reference)
    assert any(
        "candidate digest" in issue for issue in verify_unit(source)["issues"]
    )


def test_candidate_becomes_stale_after_another_release_is_promoted(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    source = _operating_unit(project)
    stale = _propose(source, "stale-entry-v1")
    winner = _propose(source, "winner-entry-v1")
    winner_reference = str(winner["reference"])
    _approve(source, winner_reference)
    promote_project_knowledge(source, candidate=winner_reference)

    with pytest.raises(IntegrityError, match="candidate is stale"):
        _approve(source, str(stale["reference"]))


def test_rejected_knowledge_decision_cannot_promote_candidate(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    source = _operating_unit(project)
    proposed = _propose(source, "rejected-entry-v1")
    reference = str(proposed["reference"])
    record_decision(
        source,
        gate="knowledge",
        outcome="rejected",
        summary="공통 규칙으로 승격하지 않는다.",
        rationale=["아직 다른 Unit에 적용할 근거가 부족하다."],
        alternatives=[],
        tradeoffs=[],
        risks=["현재 Unit에만 남는다."],
        references=[reference],
        decided_by="human-reviewer",
    )

    with pytest.raises(LifecycleError, match="latest knowledge Decision"):
        promote_project_knowledge(source, candidate=reference)
