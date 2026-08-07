from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


# A lock is held only for the few filesystem operations that rewrite one
# artifact. Anything older than this was abandoned by a process that died while
# holding it, and must not block the Unit or Foundation forever.
LOCK_STALE_SECONDS = 300
# Held sections are short, so a caller that arrives during one should wait for
# it rather than fail. Only a genuinely long-running holder exceeds this.
LOCK_WAIT_SECONDS = 5.0
_POLL_SECONDS = 0.02


class LockUnavailable(RuntimeError):
    """Raised when another process is already mutating the same artifact."""


def _stale(lock_path: Path) -> bool:
    """Report whether an existing lock was abandoned and may be reclaimed."""
    try:
        return time.time() - lock_path.stat().st_mtime >= LOCK_STALE_SECONDS
    except FileNotFoundError:
        return True


def _same_file(first: Path, second: Path) -> bool:
    try:
        return os.stat(first).st_ino == os.stat(second).st_ino
    except FileNotFoundError:
        return False


def _same_claim(first: Path, second: Path) -> bool:
    try:
        return first.read_bytes() == second.read_bytes()
    except (FileNotFoundError, OSError):
        return False


def _try_claim(claim: Path, lock_path: Path) -> bool:
    """Attempt one atomic acquisition. Return True when the lock is ours.

    ``os.link`` fails atomically when the target exists, and comparing inodes
    afterwards detects the one case a bare ``O_EXCL`` retry cannot: two
    processes both deciding an abandoned lock is stale, both unlinking it, and
    both creating what each believes is its own lock.
    """
    try:
        os.link(claim, lock_path)
    except FileExistsError:
        return False
    except (OSError, NotImplementedError):
        # Filesystems without hard links fall back to an exclusive create. The
        # reclaim race stays possible there, but a lock is still better than none.
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(claim.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            lock_path.unlink(missing_ok=True)
            raise
        return True
    return _same_file(claim, lock_path)


def _acquire(lock_path: Path, timeout: float = LOCK_WAIT_SECONDS) -> Path | None:
    """Take ``lock_path``, returning the claim file that proves ownership."""
    claim_id = uuid.uuid4().hex
    claim = lock_path.with_name(f"{lock_path.name}.{claim_id}")
    claim.parent.mkdir(parents=True, exist_ok=True)
    claim.write_text(f"{os.getpid()}:{claim_id}\n", encoding="utf-8")
    deadline = time.monotonic() + max(timeout, 0.0)
    try:
        while True:
            if _try_claim(claim, lock_path):
                return claim
            if _stale(lock_path):
                lock_path.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                break
            time.sleep(_POLL_SECONDS)
    except Exception:
        claim.unlink(missing_ok=True)
        raise
    claim.unlink(missing_ok=True)
    return None


def _release(lock_path: Path, claim: Path) -> None:
    # Only drop the lock while we still hold it, so a reclaimed lock belonging
    # to another process is never deleted.
    if _same_file(claim, lock_path) or _same_claim(claim, lock_path):
        lock_path.unlink(missing_ok=True)
    claim.unlink(missing_ok=True)


@contextmanager
def file_lock(
    lock_path: str | Path,
    *,
    subject: str,
    timeout: float = LOCK_WAIT_SECONDS,
) -> Iterator[None]:
    """Serialize mutations of one artifact across processes.

    ISEKAI ledgers are read-modify-write documents, so two agents recording into
    the same Unit or Foundation would otherwise silently overwrite each other's
    records.
    """
    path = Path(lock_path)
    claim = _acquire(path, timeout)
    if claim is None:
        raise LockUnavailable(
            f"{subject} is being modified by another process; retry after it completes"
        )
    try:
        yield
    finally:
        _release(path, claim)
