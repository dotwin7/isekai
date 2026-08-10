from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from typing import Sequence

from isekai.support.files import UnsafeControlFile, read_control_file
from isekai.support.jsonio import write_bytes_atomic, write_json_atomic
from isekai.support.locking import file_lock
from isekai.support.errors import IntegrityError
from isekai.workflow.project import _context_receipt_id
from isekai.catalog.ai_dlc.routing import ALLOWED_AGENT_LEVELS, WorkRoute


def _write_json(path: Path, value: Any) -> None:
    write_json_atomic(path, value)


def _restore_snapshots(
    snapshots: Sequence[tuple[Path, bytes]],
    label: str,
    cause: Exception,
) -> None:
    """Restore pre-mutation file contents; raise IntegrityError if any restore fails."""
    errors: list[str] = []
    for path, content in snapshots:
        try:
            write_bytes_atomic(path, content)
        except Exception as exc:  # pragma: no cover - secondary filesystem failure
            errors.append(f"{path}: {exc}")
    if errors:
        raise IntegrityError(
            f"{label} failed and could not be restored: " + "; ".join(errors)
        ) from cause


PROTECTED_UNIT_ARTIFACTS = {
    "unit.json",
    "context-receipt.json",
    "decisions.json",
    "amendments.json",
    "execution-envelope.json",
    "execution-authorizations.json",
    "checkpoint.json",
    "evaluations/criteria.json",
    "evidence/verification.json",
}
PROTECTED_UNIT_ARTIFACT_PREFIXES = (
    "evidence/records/",
    "execution-authorization-records/",
)
UNIT_LOCK_NAME = ".isekai-unit.lock"
CANONICAL_UNIT_ID = re.compile(r"UNIT-\d{8}-[A-F0-9]{32}")


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
        raise IntegrityError(f"Unit artifact path must stay inside the Unit: {relative}")
    candidate = unit_dir
    for part in relative_path.parts:
        candidate /= part
        if candidate.is_symlink():
            raise IntegrityError(f"Unit artifact path contains a symlink: {relative}")
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
        raise IntegrityError(f"missing Unit file: {relative}") from exc
    except UnsafeControlFile as exc:
        raise IntegrityError(str(exc)) from exc
    except OSError as exc:
        raise IntegrityError(f"cannot safely read Unit file {relative}: {exc}") from exc


def _unit_text(unit_dir: Path, relative: str) -> str:
    try:
        return _unit_bytes(unit_dir, relative).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IntegrityError(f"invalid UTF-8 in Unit file {relative}") from exc


def _unit_json(unit_dir: Path, relative: str) -> dict[str, Any]:
    try:
        value = json.loads(_unit_text(unit_dir, relative))
    except json.JSONDecodeError as exc:
        raise IntegrityError(f"invalid Unit JSON in {relative}: {exc}") from exc
    if not isinstance(value, dict):
        raise IntegrityError(f"Unit JSON must be an object: {relative}")
    return value


def _is_unit_directory(directory: Path) -> bool:
    """Return whether a directory contains an initialized ISEKAI Unit.

    The directory name is deliberately ignored here. Cross-Unit protection
    must continue to recognize a Unit after its directory is renamed.
    """

    try:
        unit = _unit_json(directory, "unit.json")
    except ValueError:
        return False
    unit_id = unit.get("id")
    return isinstance(unit_id, str) and CANONICAL_UNIT_ID.fullmatch(unit_id) is not None


def _is_canonical_unit_directory(directory: Path) -> bool:
    """Return whether a Unit's canonical ID matches its directory name."""

    if not _is_unit_directory(directory):
        return False
    unit = _unit_json(directory, "unit.json")
    return str(unit["id"]).casefold() == directory.name.casefold()


def _unit_maximum_agent_level(unit_dir: Path) -> str:
    receipt = _unit_json(unit_dir, "context-receipt.json")
    level = receipt.get("maximum_agent_level")
    if level not in ALLOWED_AGENT_LEVELS:
        raise IntegrityError(
            "Context Receipt maximum_agent_level must be one of: "
            + ", ".join(sorted(ALLOWED_AGENT_LEVELS))
        )
    return str(level)


def _unit_preflight_issues(unit_dir: Path) -> list[str]:
    issues: list[str] = []
    for relative in sorted(UNIT_REQUIRED_FILES):
        try:
            path = _unit_path_without_symlinks(unit_dir, relative)
            if path.exists() or path.is_symlink():
                _unit_bytes(unit_dir, relative)
        except IntegrityError as exc:
            issues.append(str(exc))
    if issues:
        return issues
    try:
        unit = _unit_json(unit_dir, "unit.json")
    except IntegrityError as exc:
        return [str(exc)]
    scope = unit.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        issues.append("Unit scope is missing or ambiguous")
    try:
        receipt = _unit_json(unit_dir, "context-receipt.json")
    except IntegrityError as exc:
        return issues + [str(exc)]
    required_context = {
        "project_id",
        "route",
        "rules",
        "profiles",
        "extensions",
        "foundation_version",
        "foundation_digest",
        "maximum_agent_level",
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
    source_manifest = receipt.get("source_manifest")
    if not isinstance(source_manifest, str) or not source_manifest.strip():
        issues.append("Context Receipt source_manifest must be a non-empty string")
    else:
        portable_source = source_manifest.replace("\\", "/")
        source_is_absolute = portable_source.startswith("/") or bool(
            re.match(r"^[A-Za-z]:", portable_source)
        )
        source_base = receipt.get("source_manifest_base")
        if source_base not in {None, "unit", "absolute"}:
            issues.append("Context Receipt has an unsupported source_manifest_base")
        elif source_base == "unit" and source_is_absolute:
            issues.append(
                "Context Receipt unit-based source_manifest must be relative"
            )
        elif source_base == "absolute" and not source_is_absolute:
            issues.append(
                "Context Receipt absolute source_manifest must be absolute"
            )
    maximum_agent_level = receipt.get("maximum_agent_level")
    if maximum_agent_level not in ALLOWED_AGENT_LEVELS:
        issues.append(
            "Context Receipt maximum_agent_level must be one of: "
            + ", ".join(sorted(ALLOWED_AGENT_LEVELS))
        )
    if receipt.get("foundation_version") != unit.get("foundation_version"):
        issues.append("Context Receipt foundation_version does not match Unit")
    if receipt.get("foundation_digest") != unit.get("foundation_digest"):
        issues.append("Context Receipt foundation_digest does not match Unit")
    from isekai.workflow.project_knowledge import (
        project_knowledge_binding_issues,
        project_knowledge_receipt_issues,
    )

    issues.extend(
        "Context Receipt Project Knowledge: " + issue
        for issue in project_knowledge_receipt_issues(
            receipt.get("project_knowledge"),
            project_id=str(unit.get("project_id")),
        )
    )
    if not project_knowledge_receipt_issues(
        receipt.get("project_knowledge"),
        project_id=str(unit.get("project_id")),
    ):
        issues.extend(
            "Context Receipt Project Knowledge: " + issue
            for issue in project_knowledge_binding_issues(unit_dir, receipt)
        )
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


def _parse_iso_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_iso_timestamp(value: Any) -> bool:
    return _parse_iso_timestamp(value) is not None
