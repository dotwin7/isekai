from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..support.jsonio import (
    UnsafeWritePath,
    unlink_file_beneath,
    write_bytes_atomic_beneath,
    write_json_atomic_beneath,
)
from .release import DistributionError, _component_root


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def adapter_skill_source(adapter_source: Path, runtime: str) -> Path:
    if (adapter_source / "SKILL.md").is_file():
        return adapter_source
    return _component_root(
        adapter_source,
        "skills/isekai",
        label=f"adapter:{runtime}.skill_path",
    )


def write_project_json(
    project_root: Path,
    relative: str | Path,
    value: dict[str, Any],
) -> None:
    try:
        write_json_atomic_beneath(
            project_root,
            relative,
            value,
            create_parents=True,
        )
    except UnsafeWritePath as exc:
        raise DistributionError(str(exc)) from exc


def write_project_bytes(
    project_root: Path,
    relative: str | Path,
    content: bytes,
    *,
    mode: int | None = None,
) -> None:
    try:
        write_bytes_atomic_beneath(
            project_root,
            relative,
            content,
            mode=mode,
            create_parents=True,
        )
    except UnsafeWritePath as exc:
        raise DistributionError(str(exc)) from exc


def unlink_project_file(project_root: Path, relative: str | Path) -> None:
    try:
        unlink_file_beneath(project_root, relative, missing_ok=True)
    except UnsafeWritePath as exc:
        raise DistributionError(str(exc)) from exc


def restore_project_file(
    project_root: Path,
    relative: str | Path,
    content: bytes | None,
) -> None:
    try:
        if content is None:
            unlink_file_beneath(project_root, relative, missing_ok=True)
        else:
            write_bytes_atomic_beneath(project_root, relative, content)
    except UnsafeWritePath as exc:
        raise DistributionError(str(exc)) from exc
