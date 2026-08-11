from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

import isekai.workflow.project_knowledge as knowledge_module
from isekai.workflow import (
    project_knowledge_status,
    promote_project_knowledge,
    propose_project_knowledge,
)
from isekai.workflow.errors import IntegrityError

from test_core_workflow import make_project
from test_project_knowledge import _approve, _entry, _operating_unit


def test_concurrent_proposals_from_one_unit_preserve_both_candidates(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    source = _operating_unit(project)
    barrier = threading.Barrier(2)

    def propose(entry_id: str) -> dict[str, Any]:
        barrier.wait()
        return propose_project_knowledge(
            source,
            entries=[_entry(entry_id)],
            proposed_by="concurrent-agent",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(propose, ["concurrent-entry-a", "concurrent-entry-b"])
        )

    assert len({result["candidate"]["id"] for result in results}) == 2
    status = project_knowledge_status(project)
    assert status["candidate_count"] == 2
    assert status["candidate_status_counts"]["pending-decision"] == 2


def test_concurrent_promotions_allow_one_winner_and_make_other_stale(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    first_unit = _operating_unit(project, "첫 번째 동시 승격 Unit")
    second_unit = _operating_unit(project, "두 번째 동시 승격 Unit")
    first = propose_project_knowledge(
        first_unit,
        entries=[_entry("promotion-race-a")],
        proposed_by="first-agent",
    )
    second = propose_project_knowledge(
        second_unit,
        entries=[_entry("promotion-race-b")],
        proposed_by="second-agent",
    )
    first_reference = str(first["reference"])
    second_reference = str(second["reference"])
    _approve(first_unit, first_reference)
    _approve(second_unit, second_reference)
    barrier = threading.Barrier(2)

    def promote(unit: Path, reference: str) -> tuple[str, object]:
        barrier.wait()
        try:
            return "promoted", promote_project_knowledge(unit, candidate=reference)
        except Exception as exc:  # captured so both racing results can be asserted
            return "failed", exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(promote, first_unit, first_reference),
            executor.submit(promote, second_unit, second_reference),
        ]
        results = [future.result() for future in futures]

    assert [state for state, _result in results].count("promoted") == 1
    failures = [result for state, result in results if state == "failed"]
    assert len(failures) == 1
    assert isinstance(failures[0], IntegrityError)
    assert "candidate is stale" in str(failures[0])
    status = project_knowledge_status(project)
    assert status["candidate_status_counts"]["promoted"] == 1
    assert status["candidate_status_counts"]["stale"] == 1
    assert len(status["current_release"]["entries"]) == 1


def test_promotion_restores_catalog_when_atomic_writer_reports_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    source = _operating_unit(project)
    proposed = propose_project_knowledge(
        source,
        entries=[_entry("rollback-entry")],
        proposed_by="learning-agent",
    )
    reference = str(proposed["reference"])
    _approve(source, reference)
    original_write = knowledge_module._write_project_json

    def write_then_fail(
        root: Path,
        relative: str,
        value: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        original_write(root, relative, value, **kwargs)
        if relative.endswith("catalog.json"):
            raise OSError("simulated post-replace failure")

    monkeypatch.setattr(knowledge_module, "_write_project_json", write_then_fail)

    with pytest.raises(OSError, match="post-replace failure"):
        promote_project_knowledge(source, candidate=reference)

    assert not (project.parent / "project-knowledge/catalog.json").exists()
    status = project_knowledge_status(project)
    assert status["candidate_status_counts"]["approved"] == 1


def test_proposal_removes_candidate_when_atomic_writer_reports_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    source = _operating_unit(project)
    original_write = knowledge_module._write_project_json

    def write_then_fail(
        root: Path,
        relative: str,
        value: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        original_write(root, relative, value, **kwargs)
        if "/candidates/" in relative:
            raise OSError("simulated candidate post-replace failure")

    monkeypatch.setattr(knowledge_module, "_write_project_json", write_then_fail)

    with pytest.raises(OSError, match="candidate post-replace failure"):
        propose_project_knowledge(
            source,
            entries=[_entry("proposal-rollback-entry")],
            proposed_by="learning-agent",
        )

    candidates = project.parent / "project-knowledge/candidates"
    assert candidates.is_dir()
    assert list(candidates.iterdir()) == []
    assert project_knowledge_status(project)["candidate_count"] == 0
