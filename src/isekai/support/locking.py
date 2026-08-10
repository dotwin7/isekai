from __future__ import annotations

import errno
import os
import stat
import sys
import time
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .files import metadata_is_path_alias


# Held sections are short, so a caller that arrives during one should wait for
# it rather than fail. Only a genuinely long-running holder exceeds this.
LOCK_WAIT_SECONDS = 5.0
_POLL_SECONDS = 0.02


class LockUnavailable(RuntimeError):
    """Raised when another process is already mutating the same artifact."""


def _same_open_file(descriptor: int, path: Path) -> bool:
    try:
        opened = os.fstat(descriptor)
        current = path.stat()
    except FileNotFoundError:
        return False
    return (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)


@dataclass(frozen=True)
class _LockClaim:
    descriptor: int


def _try_os_lock(descriptor: int) -> bool:
    if sys.platform == "win32":  # pragma: no cover - exercised by Windows CI/users
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        return False
    return True


def _unlock(descriptor: int) -> None:
    if sys.platform == "win32":  # pragma: no cover - exercised by Windows CI/users
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _acquire(lock_path: Path, timeout: float = LOCK_WAIT_SECONDS) -> _LockClaim | None:
    """Take an OS-managed advisory lock, recovering safely after process death."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                return None
            raise
        try:
            path_metadata = lock_path.lstat()
        except FileNotFoundError:
            path_metadata = None
        if path_metadata is None:
            os.close(descriptor)
            if time.monotonic() >= deadline:
                return None
            time.sleep(_POLL_SECONDS)
            continue
        if metadata_is_path_alias(path_metadata):
            os.close(descriptor)
            return None
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            return None
        if metadata.st_nlink == 0:
            # A POSIX owner can unlink the shared path just before releasing
            # its advisory lock. A waiter that already opened that inode sees
            # link count zero; this is a normal release race, not a hardlink
            # attack. Retry against the new path instead of failing early.
            os.close(descriptor)
            if time.monotonic() >= deadline:
                return None
            time.sleep(_POLL_SECONDS)
            continue
        if metadata.st_nlink != 1:
            os.close(descriptor)
            return None
        if metadata.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        if _try_os_lock(descriptor):
            # A previous owner may have unlinked this inode while a waiter had it
            # open. Never enter through an orphaned descriptor while a new path
            # can be locked independently.
            if _same_open_file(descriptor, lock_path):
                return _LockClaim(descriptor)
            _unlock(descriptor)
        os.close(descriptor)
        if time.monotonic() >= deadline:
            return None
        time.sleep(_POLL_SECONDS)


def _release(lock_path: Path, claim: _LockClaim) -> None:
    if sys.platform == "win32":  # pragma: no cover - exercised by Windows CI/users
        # Windows does not allow unlinking an open file. Release and close first;
        # if a waiter acquired it in the meantime, deletion fails and the shared
        # lock path safely remains for that owner.
        try:
            _unlock(claim.descriptor)
        finally:
            os.close(claim.descriptor)
        try:
            lock_path.unlink(missing_ok=True)
        except PermissionError:
            pass
        return
    try:
        if _same_open_file(claim.descriptor, lock_path):
            lock_path.unlink(missing_ok=True)
    finally:
        try:
            _unlock(claim.descriptor)
        finally:
            os.close(claim.descriptor)


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
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        pass
    else:
        if metadata_is_path_alias(metadata):
            raise LockUnavailable(
                f"{subject} lock path must not be a symlink or reparse point"
            )
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise LockUnavailable(
                f"{subject} lock path must be a single-link regular file"
            )
    claim = _acquire(path, timeout)
    if claim is None:
        raise LockUnavailable(
            f"{subject} is being modified by another process; retry after it completes"
        )
    try:
        yield
    finally:
        _release(path, claim)
