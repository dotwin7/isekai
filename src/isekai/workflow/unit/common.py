from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ...support.files import UnsafeControlFile, read_control_file
from ...support.jsonio import write_json_atomic
from ...support.locking import file_lock
from ..project import _context_receipt_id
from ..routing import WorkRoute


def _write_json(path: Path, value: Any) -> None:
    write_json_atomic(path, value)


PROTECTED_UNIT_ARTIFACTS = {
    "unit.json",
    "context-receipt.json",
    "decisions.json",
    "execution-envelope.json",
    "execution-authorizations.json",
    "checkpoint.json",
    "evaluations/criteria.json",
    "evidence/verification.json",
}
PROTECTED_UNIT_ARTIFACT_PREFIXES = ("evidence/records/",)
UNIT_LOCK_NAME = ".isekai-unit.lock"


@contextmanager
def unit_lock(unit_dir: Path):
    """Serialize every mutation of one Unit.

    ``decisions.json`` and ``unit.json`` are read-modify-write ledgers. Without a
    shared lock two agents working the same Unit overwrite each other's records,
    and the loser's postflight cannot tell, because it only sees that its own
    record landed.
    """
    with file_lock(unit_dir / UNIT_LOCK_NAME, subject=f"Unit {unit_dir.name}"):
        yield


UNIT_REQUIRED_FILES = {
    "unit.json",
    "intent.md",
    "requirements.md",
    "decisions.json",
    "architecture.md",
    "plan.md",
    "acceptance.md",
    "evaluations/criteria.json",
    "evidence/verification.json",
    "release.md",
    "operations.md",
    "implementation-guide.md",
    "checkpoint.json",
    "context-receipt.json",
    "execution-envelope.json",
    "execution-authorizations.json",
}


def _unit_path_without_symlinks(unit_dir: Path, relative: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"Unit artifact path must stay inside the Unit: {relative}")
    candidate = unit_dir
    for part in relative_path.parts:
        candidate /= part
        if candidate.is_symlink():
            raise ValueError(f"Unit artifact path contains a symlink: {relative}")
    return candidate


def _unit_bytes(unit_dir: Path, relative: str) -> bytes:
    path = _unit_path_without_symlinks(unit_dir, relative)
    try:
        return read_control_file(
            path,
            root=unit_dir,
            label=f"Unit artifact {relative}",
        )
    except FileNotFoundError as exc:
        raise ValueError(f"missing Unit file: {relative}") from exc
    except UnsafeControlFile as exc:
        raise ValueError(str(exc)) from exc
    except OSError as exc:
        raise ValueError(f"cannot safely read Unit file {relative}: {exc}") from exc


def _unit_text(unit_dir: Path, relative: str) -> str:
    try:
        return _unit_bytes(unit_dir, relative).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid UTF-8 in Unit file {relative}") from exc


def _unit_json(unit_dir: Path, relative: str) -> dict[str, Any]:
    try:
        value = json.loads(_unit_text(unit_dir, relative))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Unit JSON in {relative}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Unit JSON must be an object: {relative}")
    return value


def _unit_preflight_issues(unit_dir: Path) -> list[str]:
    issues: list[str] = []
    for relative in sorted(UNIT_REQUIRED_FILES):
        try:
            path = _unit_path_without_symlinks(unit_dir, relative)
            if path.exists() or path.is_symlink():
                _unit_bytes(unit_dir, relative)
        except ValueError as exc:
            issues.append(str(exc))
    if issues:
        return issues
    try:
        unit = _unit_json(unit_dir, "unit.json")
    except ValueError as exc:
        return [str(exc)]
    scope = unit.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        issues.append("Unit scope is missing or ambiguous")
    try:
        receipt = _unit_json(unit_dir, "context-receipt.json")
    except ValueError as exc:
        return issues + [str(exc)]
    required_context = {
        "project_id",
        "route",
        "rules",
        "profiles",
        "extensions",
        "foundation_version",
        "foundation_digest",
        "source_manifest",
    }
    missing_context = sorted(required_context - receipt.keys())
    if missing_context:
        issues.append(
            f"Context Receipt missing fields: {', '.join(missing_context)}"
        )
    if receipt.get("project_id") != unit.get("project_id"):
        issues.append("Context Receipt project_id does not match Unit")
    if receipt.get("document_language") != unit.get("document_language"):
        issues.append("Context Receipt document_language does not match Unit")
    if receipt.get("route") != WorkRoute.UNIT.value:
        issues.append("Context Receipt route must be unit")
    if receipt.get("foundation_version") != unit.get("foundation_version"):
        issues.append("Context Receipt foundation_version does not match Unit")
    if receipt.get("foundation_digest") != unit.get("foundation_digest"):
        issues.append("Context Receipt foundation_digest does not match Unit")
    receipt_id = receipt.get("receipt_id")
    if not isinstance(receipt_id, str) or receipt_id != _context_receipt_id(receipt):
        issues.append("Context Receipt receipt_id does not match its bound context")
    rules = receipt.get("rules")
    if not isinstance(rules, list) or not rules:
        issues.append("Context Receipt has no full applied rules")
    else:
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                issues.append(f"Context rule {index} must be an object")
                continue
            if rule.get("level") == "MUST" and not isinstance(rule.get("condition"), dict):
                issues.append(f"Context MUST rule {index} has no machine condition")
    return issues
