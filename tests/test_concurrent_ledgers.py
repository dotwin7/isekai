from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

from isekai.foundation import record_foundation_decision
from isekai.locking import LockUnavailable, file_lock
from isekai.workflow import authorize_action, record_decision, transition_unit

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
            summary=f"architecture decision {index}",
            rationale=["bounded change"],
            alternatives=[{"option": "defer", "reason": "rejected"}],
            tradeoffs=["scope"],
            risks=["none"],
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
            summary=f"waiting writer {index}",
            rationale=["bounded change"],
            alternatives=[{"option": "defer", "reason": "rejected"}],
            tradeoffs=["scope"],
            risks=["none"],
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


def test_file_lock_fallback_releases_its_owned_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import isekai.support.locking as locking

    lock = tmp_path / "artifact.lock"
    monkeypatch.setattr(
        locking.os,
        "link",
        lambda _claim, _lock: (_ for _ in ()).throw(OSError("no hard links")),
    )

    with file_lock(lock, subject="artifact"):
        assert lock.exists()

    assert list(tmp_path.iterdir()) == []
