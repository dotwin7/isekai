from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .release import (
    LOCK_NAME,
    LOCK_SCHEMA_VERSION,
    MANAGED_ROOT,
    RUNTIMES,
    DistributionError,
    _read_control_json,
    _safe_relative_path,
)


INSTALL_LOCK_NAME = ".isekai-install.lock"
WORKSPACE_ADAPTER_PATHS = {
    "codex": Path(".agents/skills/isekai"),
    "claude": Path(".claude/skills/isekai"),
    "kiro": Path(".kiro/skills/isekai"),
}
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-fA-F]{64}")
_COMMIT_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")


def _non_empty_string_issues(value: object, *, label: str) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [f"lock {label} must be a non-empty string"]
    return []


def _digest_issues(value: object, *, label: str) -> list[str]:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        return [f"lock {label} must be a sha256 digest"]
    return []


def _relative_path_issues(value: object, *, label: str) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [f"lock {label} must be a non-empty relative path"]
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return [f"lock {label} must stay inside the project"]
    return []


def _component_lock_issues(
    value: object,
    *,
    label: str,
    source_digest: bool,
) -> list[str]:
    if not isinstance(value, dict):
        return [f"lock {label} must be an object"]
    issues: list[str] = []
    issues.extend(
        _non_empty_string_issues(value.get("version"), label=f"{label}.version")
    )
    issues.extend(_relative_path_issues(value.get("path"), label=f"{label}.path"))
    issues.extend(_digest_issues(value.get("digest"), label=f"{label}.digest"))
    if source_digest:
        issues.extend(
            _digest_issues(
                value.get("source_digest"),
                label=f"{label}.source_digest",
            )
        )
    return issues


def _install_lock_issues(lock: object) -> list[str]:
    if not isinstance(lock, dict):
        return ["isekai.lock.json must be a JSON object"]
    issues: list[str] = []
    if lock.get("schema_version") != LOCK_SCHEMA_VERSION:
        issues.append("unsupported isekai.lock.json schema_version")
    issues.extend(_non_empty_string_issues(lock.get("release"), label="release"))
    issues.extend(
        _non_empty_string_issues(
            lock.get("protocol_version"), label="protocol_version"
        )
    )
    if "marketplace" in lock:
        issues.extend(
            _non_empty_string_issues(lock.get("marketplace"), label="marketplace")
        )

    source = lock.get("source")
    if not isinstance(source, dict):
        issues.append("lock source must be an object")
    else:
        issues.extend(_non_empty_string_issues(source.get("git"), label="source.git"))
        issues.extend(_non_empty_string_issues(source.get("ref"), label="source.ref"))
        if isinstance(source.get("ref"), str) and source["ref"].startswith("-"):
            issues.append("lock source.ref cannot start with '-'")
        commit = source.get("commit")
        if not isinstance(commit, str) or _COMMIT_PATTERN.fullmatch(commit) is None:
            issues.append("lock source.commit must be a full 40- or 64-character hash")

    issues.extend(
        _component_lock_issues(
            lock.get("core"),
            label="core",
            source_digest=True,
        )
    )
    catalog_lock = lock.get("entries")
    if catalog_lock is not None:
        issues.extend(
            _component_lock_issues(
                catalog_lock,
                label="catalog",
                source_digest=True,
            )
        )
        if isinstance(catalog_lock, dict):
            issues.extend(
                _non_empty_string_issues(catalog_lock.get("id"), label="catalog.id")
            )
    foundation = lock.get("foundation")
    issues.extend(
        _component_lock_issues(
            foundation,
            label="foundation",
            source_digest=False,
        )
    )
    if isinstance(foundation, dict):
        issues.extend(
            _non_empty_string_issues(foundation.get("id"), label="foundation.id")
        )
        issues.extend(
            _non_empty_string_issues(
                foundation.get("source_release"),
                label="foundation.source_release",
            )
        )

    adapters = lock.get("adapters")
    if not isinstance(adapters, dict):
        issues.append("lock adapters must be an object")
    else:
        for runtime, entry in sorted(adapters.items(), key=lambda item: str(item[0])):
            if runtime not in RUNTIMES:
                issues.append(f"unknown adapter in lock: {runtime}")
                continue
            issues.extend(
                _component_lock_issues(
                    entry,
                    label=f"adapter:{runtime}",
                    source_digest=True,
                )
            )
            if isinstance(entry, dict) and "installed_version" in entry:
                issues.extend(
                    _non_empty_string_issues(
                        entry.get("installed_version"),
                        label=f"adapter:{runtime}.installed_version",
                    )
                )
            if isinstance(entry, dict):
                has_workspace_path = "workspace_path" in entry
                has_workspace_digest = "workspace_digest" in entry
                if has_workspace_path != has_workspace_digest:
                    issues.append(
                        f"lock adapter:{runtime} workspace_path and workspace_digest "
                        "must be supplied together"
                    )
                elif has_workspace_path:
                    issues.extend(
                        _relative_path_issues(
                            entry.get("workspace_path"),
                            label=f"adapter:{runtime}.workspace_path",
                        )
                    )
                    issues.extend(
                        _digest_issues(
                            entry.get("workspace_digest"),
                            label=f"adapter:{runtime}.workspace_digest",
                        )
                    )

    rollback = lock.get("rollback")
    if rollback is not None:
        if not isinstance(rollback, dict):
            issues.append("lock rollback must be an object")
        else:
            if rollback.get("path") != f"{MANAGED_ROOT}/rollback":
                issues.append(f"lock rollback.path must be {MANAGED_ROOT}/rollback")
            issues.extend(
                _digest_issues(rollback.get("digest"), label="rollback.digest")
            )
    return list(dict.fromkeys(issues))


def _load_install_lock_path(path: Path) -> dict[str, Any]:
    lock = _read_control_json(
        path,
        root=path.parent,
        label=LOCK_NAME,
    )
    issues = _install_lock_issues(lock)
    if issues:
        raise DistributionError("invalid isekai.lock.json: " + "; ".join(issues))
    return lock


def load_install_lock(project: str | Path) -> dict[str, Any] | None:
    requested = Path(project).expanduser()
    if requested.name == LOCK_NAME:
        path = requested.absolute()
    else:
        path = requested.resolve() / LOCK_NAME
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    return _load_install_lock_path(path)


def _project_path_without_symlinks(
    project_root: Path, relative: Path, *, label: str
) -> Path:
    lexical = project_root
    for part in relative.parts:
        lexical = lexical / part
        if lexical.is_symlink():
            raise DistributionError(f"{label} contains a symlink: {relative}")
    return lexical


def _installed_path(project_root: Path, value: object, *, label: str) -> Path:
    relative = _safe_relative_path(value, label=label)
    lexical = _project_path_without_symlinks(project_root, relative, label=label)
    target = lexical.resolve()
    try:
        target.relative_to(project_root.resolve())
    except ValueError as exc:  # pragma: no cover - defensive
        raise DistributionError(f"{label} escapes the project root") from exc
    return target


def _workspace_adapter_owned(adapters: object, runtime: str) -> bool:
    if not isinstance(adapters, dict):
        return False
    entry = adapters.get(runtime)
    expected = WORKSPACE_ADAPTER_PATHS[runtime].as_posix()
    return isinstance(entry, dict) and (
        entry.get("path") == expected or entry.get("workspace_path") == expected
    )
