from __future__ import annotations

import json
import os
import shutil
import stat
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest

from isekai.foundation import record_foundation_decision
from isekai.locking import LockUnavailable, file_lock
from isekai.support.files import metadata_is_path_alias
from isekai.workflow import (
    authorize_action,
    record_decision,
    transition_unit,
    verify_unit,
)

from test_core_workflow import ROOT
from test_execution_envelope import approve_inception, make_enveloped_unit


def _run_concurrently(work, count: int) -> list[BaseException]:
    errors: list[BaseException] = []
    barrier = threading.Barrier(count)

    def target(index: int) -> None:
        barrier.wait()
        try:
            work(index)
        except BaseException as exc:  # noqa: BLE001 - recorded for assertions
            errors.append(exc)

    threads = [threading.Thread(target=target, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def test_concurrent_decisions_are_never_silently_dropped(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)

    def record(index: int) -> None:
        record_decision(
            unit,
            gate="architecture",
            outcome="approved",
            summary=f"아키텍처 결정 {index}",
            rationale=["변경 범위를 제한했다."],
            alternatives=[
                {"option": "연기한다.", "reason": "동시성 검증을 위해 기각했다."}
            ],
            tradeoffs=["범위를 제한한다."],
            risks=["별도 위험이 없다."],
            references=["architecture.md"],
            decided_by="human-reviewer",
        )

    errors = _run_concurrently(record, 8)
    entries = json.loads((unit / "decisions.json").read_text(encoding="utf-8"))["decisions"]
    architecture = [item for item in entries if item["gate"] == "architecture"]

    # Every writer either persists its record or fails loudly. Before the Unit
    # lock, 8 concurrent writers left 1 record and only 2 of the 7 losers knew.
    assert len(architecture) == 8 - len(errors)
    assert all(isinstance(error, (ValueError, LockUnavailable)) for error in errors)
    assert len({item["id"] for item in architecture}) == len(architecture)


def test_a_waiting_caller_acquires_the_lock_instead_of_failing(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)

    def record(index: int) -> None:
        record_decision(
            unit,
            gate="architecture",
            outcome="approved",
            summary=f"대기 중인 기록자 {index}",
            rationale=["변경 범위를 제한했다."],
            alternatives=[
                {"option": "연기한다.", "reason": "동시성 검증을 위해 기각했다."}
            ],
            tradeoffs=["범위를 제한한다."],
            risks=["별도 위험이 없다."],
            references=["architecture.md"],
            decided_by="human-reviewer",
        )

    errors = _run_concurrently(record, 8)
    entries = json.loads((unit / "decisions.json").read_text(encoding="utf-8"))["decisions"]

    # Held sections are short, so contending writers should queue, not fail.
    assert errors == []
    assert len([item for item in entries if item["gate"] == "architecture"]) == 8


def test_concurrent_authorizations_never_exceed_the_iteration_budget(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    granted: list[dict] = []

    def request(index: int) -> None:
        result = authorize_action(unit, action="edit", target=f"src/file{index}.py")
        if result["allowed"]:
            granted.append(result)

    _run_concurrently(request, 8)
    ledger = json.loads(
        (unit / "execution-authorizations.json").read_text(encoding="utf-8")
    )

    assert len(granted) <= 3  # max_iterations from the shared fixture
    assert len(ledger["grants"]) == len(granted)
    assert [grant["iteration"] for grant in ledger["grants"]] == list(
        range(1, len(granted) + 1)
    )


def test_verification_waits_for_a_consistent_unit_snapshot(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    started = threading.Event()
    finished = threading.Event()
    result: dict[str, object] = {}

    def inspect() -> None:
        started.set()
        result.update(verify_unit(unit))
        finished.set()

    with file_lock(unit / ".isekai-unit.lock", subject="Unit mutation"):
        thread = threading.Thread(target=inspect)
        thread.start()
        assert started.wait(timeout=1)
        assert not finished.wait(timeout=0.05)

    thread.join(timeout=2)
    assert finished.is_set()
    assert "valid" in result


def test_concurrent_transitions_apply_exactly_one_edge(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    applied: list[dict] = []

    def move(_: int) -> None:
        try:
            applied.append(transition_unit(unit, "inception"))
        except (ValueError, LockUnavailable):
            pass

    _run_concurrently(move, 6)
    status = json.loads((unit / "unit.json").read_text(encoding="utf-8"))["status"]

    assert len(applied) == 1
    assert status == "inception"


def test_concurrent_foundation_decisions_are_all_recorded(tmp_path: Path) -> None:
    foundation = tmp_path / "foundation"
    shutil.copytree(ROOT / "foundation", foundation)
    release = json.loads((foundation / "release.json").read_text(encoding="utf-8"))
    release["status"] = "draft"
    (foundation / "release.json").write_text(
        json.dumps(release, indent=2) + "\n", encoding="utf-8"
    )
    (foundation / "decisions.json").unlink(missing_ok=True)

    def record(index: int) -> None:
        record_foundation_decision(
            foundation,
            outcome="approved",
            summary=f"foundation decision {index}",
            decided_by="human-reviewer",
        )

    errors = _run_concurrently(record, 6)
    entries = json.loads((foundation / "decisions.json").read_text(encoding="utf-8"))[
        "decisions"
    ]

    assert len(entries) == 6 - len(errors)
    assert len({item["id"] for item in entries}) == len(entries)


def test_file_lock_reports_a_live_holder_instead_of_corrupting(tmp_path: Path) -> None:
    lock = tmp_path / "artifact.lock"

    with file_lock(lock, subject="artifact"):
        with pytest.raises(LockUnavailable, match="another process"):
            with file_lock(lock, subject="artifact", timeout=0):
                pass

    # Releasing must leave no lock or claim files behind.
    assert list(tmp_path.iterdir()) == []


def test_file_lock_release_does_not_delete_a_reclaimed_lock(tmp_path: Path) -> None:
    from isekai.locking import _acquire, _release

    lock = tmp_path / "artifact.lock"
    first = _acquire(lock)
    assert first is not None
    lock.unlink()
    second = _acquire(lock)
    assert second is not None

    # The first holder lost the lock; releasing must not remove the new one.
    _release(lock, first)
    assert lock.exists()

    _release(lock, second)
    assert not lock.exists()


def test_file_lock_retries_an_inode_unlinked_during_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from isekai.locking import _acquire, _release
    from isekai.support import locking as locking_module

    lock = tmp_path / "artifact.lock"
    real_fstat = locking_module.os.fstat
    reported_unlinked = False

    def fstat_with_release_race(descriptor: int) -> os.stat_result:
        nonlocal reported_unlinked
        metadata = real_fstat(descriptor)
        if reported_unlinked:
            return metadata
        reported_unlinked = True
        fields = list(metadata)
        fields[3] = 0  # st_nlink
        return os.stat_result(fields)

    monkeypatch.setattr(locking_module.os, "fstat", fstat_with_release_race)

    claim = _acquire(lock, timeout=0.2)

    assert claim is not None
    _release(lock, claim)
    assert not lock.exists()


def test_file_lock_safely_reclaims_an_abandoned_unlocked_file(tmp_path: Path) -> None:
    lock = tmp_path / "artifact.lock"
    lock.write_text("abandoned owner\n", encoding="utf-8")

    with file_lock(lock, subject="artifact"):
        assert lock.exists()

    assert list(tmp_path.iterdir()) == []


def test_file_lock_rejects_a_symlink_without_touching_its_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "external-target"
    target.write_bytes(b"")
    lock = tmp_path / "artifact.lock"
    lock.symlink_to(target)

    with pytest.raises(LockUnavailable, match="must not be a symlink"):
        with file_lock(lock, subject="artifact"):
            pass

    assert target.read_bytes() == b""
    assert lock.is_symlink()


def test_file_lock_rejects_a_hardlink_without_touching_its_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "external-target"
    target.write_bytes(b"")
    lock = tmp_path / "artifact.lock"
    lock.hardlink_to(target)

    with pytest.raises(LockUnavailable, match="single-link regular file"):
        with file_lock(lock, subject="artifact", timeout=0):
            pass

    assert target.read_bytes() == b""
    assert lock.samefile(target)


def test_windows_reparse_metadata_is_treated_as_a_path_alias() -> None:
    metadata = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o755,
        st_file_attributes=0x400,
    )

    assert metadata_is_path_alias(metadata) is True
