from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(path: str | Path, value: Any) -> Path:
    """Write ``value`` as JSON so readers never observe a partial document.

    The payload is written to a same-directory temporary file, flushed to disk,
    and moved into place with ``os.replace``. Every ISEKAI artifact that another
    session may read - locks, ledgers, Decisions, Evidence, checkpoints - goes
    through this function so an interrupted process cannot truncate a record.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    _fsync_directory(target.parent)
    return target


def write_bytes_atomic(
    path: str | Path,
    content: bytes,
    *,
    mode: int | None = None,
) -> Path:
    """Atomically restore exact file bytes, optionally preserving its mode."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    _fsync_directory(target.parent)
    return target


def _fsync_directory(directory: Path) -> None:
    """Persist the rename itself, not just the bytes it points at."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover - platforms without directory descriptors
        return
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover - filesystems that reject directory fsync
        pass
    finally:
        os.close(descriptor)
