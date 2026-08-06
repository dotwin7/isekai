"""Compatibility façade for inter-process file locking."""

from .support import locking as _implementation

LOCK_STALE_SECONDS = _implementation.LOCK_STALE_SECONDS
LOCK_WAIT_SECONDS = _implementation.LOCK_WAIT_SECONDS
LockUnavailable = _implementation.LockUnavailable
_acquire = _implementation._acquire
_release = _implementation._release
_same_file = _implementation._same_file
_stale = _implementation._stale
_try_claim = _implementation._try_claim
file_lock = _implementation.file_lock
time = _implementation.time

__all__ = ["LockUnavailable", "file_lock"]
