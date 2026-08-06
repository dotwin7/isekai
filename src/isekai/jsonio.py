"""Compatibility façade for atomic JSON utilities."""

from .support.jsonio import _fsync_directory, write_json_atomic

__all__ = ["write_json_atomic"]
