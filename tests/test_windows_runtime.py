from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from isekai.support.files import UnsafeControlFile, read_control_file
from isekai.support.locking import LockUnavailable, file_lock


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only runtime checks")


def test_windows_file_lock_queues_a_waiter(tmp_path: Path) -> None:
    lock = tmp_path / "artifact.lock"
    started = threading.Event()
    acquired = threading.Event()

    def wait_for_lock() -> None:
        started.set()
        with file_lock(lock, subject="Windows artifact"):
            acquired.set()

    with file_lock(lock, subject="Windows artifact"):
        thread = threading.Thread(target=wait_for_lock)
        thread.start()
        assert started.wait(timeout=1)
        assert not acquired.wait(timeout=0.05)

    thread.join(timeout=2)
    assert acquired.is_set()
    assert not lock.exists()


def test_windows_file_lock_reports_live_contention(tmp_path: Path) -> None:
    lock = tmp_path / "artifact.lock"

    with file_lock(lock, subject="Windows artifact"):
        with pytest.raises(LockUnavailable, match="another process"):
            with file_lock(lock, subject="Windows artifact", timeout=0):
                pass


def test_windows_control_file_rejects_directory_junction(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    control = external / "control.json"
    control.write_text('{"safe": false}\n', encoding="utf-8")
    junction = tmp_path / "junction"
    created = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(external)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    try:
        with pytest.raises(UnsafeControlFile, match="symlink|safely"):
            read_control_file(
                junction / "control.json",
                root=tmp_path,
                label="junction control",
            )
    finally:
        junction.rmdir()

    assert control.read_text(encoding="utf-8") == '{"safe": false}\n'
