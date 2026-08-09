from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

from ..support.files import UnsafeControlFile, metadata_is_path_alias, read_control_file
from .errors import IntegrityError


def safe_project_json(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            read_control_file(path, root=root, label=label).decode("utf-8")
        )
    except FileNotFoundError as exc:
        raise IntegrityError(f"missing {label}: {path}") from exc
    except UnsafeControlFile as exc:
        raise IntegrityError(str(exc)) from exc
    except OSError as exc:
        raise IntegrityError(f"cannot safely read {label}: {path}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise IntegrityError(f"{label} must be a JSON object")
    return value


def managed_project_directory(
    project_root: Path, relative: str, *, create: bool
) -> Path:
    directory = project_root / relative
    current = project_root
    for part in Path(relative).parts:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if not create:
                raise IntegrityError(f"missing managed Project Knowledge path: {current}")
            try:
                current.mkdir()
                metadata = current.lstat()
            except OSError as exc:
                raise IntegrityError(
                    f"cannot create managed Project Knowledge path: {current}: {exc}"
                ) from exc
        if metadata_is_path_alias(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise IntegrityError(
                f"managed Project Knowledge path must be a real directory: {current}"
            )
    return directory
