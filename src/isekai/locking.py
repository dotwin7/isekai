"""Compatibility façade for inter-process file locking."""

from .support import locking as _implementation

LOCK_WAIT_SECONDS = _implementation.LOCK_WAIT_SECONDS
LockUnavailable = _implementation.LockUnavailable
_acquire = _implementation._acquire
_release = _implementation._release
file_lock = _implementation.file_lock

__all__ = ["LockUnavailable", "file_lock"]
