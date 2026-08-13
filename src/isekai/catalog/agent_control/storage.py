from __future__ import annotations

import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from isekai.support.files import (
    UnsafeControlFile,
    metadata_is_path_alias,
    read_control_file,
)
from isekai.support.jsonio import UnsafeWritePath, write_json_atomic_beneath
from isekai.support.locking import LockUnavailable, rooted_file_lock
from isekai.workflow.errors import IntegrityError, WorkflowError


ENGAGEMENTS_DIRECTORY = "engagements"
ENGAGEMENT_FILE = "engagement.json"
APPROVAL_FILE = "approval.json"
EXECUTIONS_FILE = "executions.json"
LOCK_FILE = ".engagement.lock"
ENGAGEMENT_ID = re.compile(r"ENG-[0-9]{14}-[0-9a-f]{8}")


def engagement_directory(project_root: Path, engagement_id: str) -> Path:
    if ENGAGEMENT_ID.fullmatch(engagement_id) is None:
        raise WorkflowError("Agent Control engagement ID is invalid")
    return project_root / ENGAGEMENTS_DIRECTORY / engagement_id


def resolve_engagement_directory(value: str | Path) -> Path:
    """Resolve an Engagement lexically while rejecting aliased control roots."""
    directory = Path(os.path.abspath(Path(value).expanduser()))
    if (
        ENGAGEMENT_ID.fullmatch(directory.name) is None
        or directory.parent.name != ENGAGEMENTS_DIRECTORY
    ):
        raise WorkflowError("Agent Control engagement path is invalid")
    for candidate, label in (
        (directory.parent.parent, "Project"),
        (directory.parent, "engagements root"),
        (directory, "engagement"),
    ):
        try:
            metadata = candidate.lstat()
        except OSError as exc:
            raise WorkflowError(
                f"Agent Control {label} does not exist: {candidate}"
            ) from exc
        if metadata_is_path_alias(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise WorkflowError(f"Agent Control {label} must be a real directory")
    return directory


def read_json(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            read_control_file(path, root=root, label=label).decode("utf-8")
        )
    except FileNotFoundError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, UnsafeControlFile) as exc:
        raise IntegrityError(f"cannot safely read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise IntegrityError(f"{label} must be an object")
    return value


def write_json(root: Path, relative: str | Path, value: dict[str, Any]) -> None:
    try:
        write_json_atomic_beneath(
            root,
            relative,
            value,
            create_parents=True,
            parent_mode=0o700,
        )
    except UnsafeWritePath as exc:
        raise IntegrityError(str(exc)) from exc


@contextmanager
def engagement_lock(directory: Path) -> Iterator[None]:
    if not directory.is_dir():
        raise WorkflowError(f"Agent Control engagement does not exist: {directory}")
    try:
        with rooted_file_lock(
            directory,
            LOCK_FILE,
            subject="Agent Control engagement",
            parent_mode=0o700,
        ):
            yield
    except LockUnavailable as exc:
        raise WorkflowError(str(exc)) from exc


def load_engagement(directory: Path) -> dict[str, Any]:
    return read_json(
        directory / ENGAGEMENT_FILE,
        root=directory,
        label="Agent Control engagement",
    )


def load_executions(directory: Path) -> dict[str, Any]:
    return read_json(
        directory / EXECUTIONS_FILE,
        root=directory,
        label="Agent Control execution ledger",
    )
