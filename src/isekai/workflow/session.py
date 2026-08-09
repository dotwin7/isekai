from __future__ import annotations

import copy
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..support.files import metadata_is_path_alias
from ..support.jsonio import write_json_atomic
from .project import (
    _context_contract_changed_fields,
    _context_receipt_id,
    _portable_context_receipt,
    _receipt_source_manifest_path,
    resolve_context,
)
from .routing import WorkRoute
from .unit.common import (
    UNIT_LOCK_NAME,
    _unit_json,
    _unit_preflight_issues,
    unit_lock,
)
from .unit.lifecycle import unit_status


class SessionError(ValueError):
    """Raised when an ISEKAI session cannot be reconstructed safely."""


PROJECT_DISCOVERY_EXCLUDES = {
    ".git",
    ".venv",
    ".isekai-runtime",
    "node_modules",
    "dist",
    "build",
    "out",
    "target",
    "__pycache__",
    "units",
}


def _descendant_project_candidates(root: Path) -> list[Path]:
    matches: list[Path] = []
    for current, directories, files in os.walk(root):
        # Prune excluded trees during the walk. Filtering matches afterwards
        # still descends into node_modules and other large vendored trees.
        directories[:] = sorted(
            name for name in directories if name not in PROJECT_DISCOVERY_EXCLUDES
        )
        if "project.json" in files:
            matches.append(Path(current, "project.json").absolute())
    return sorted(set(matches))


def _multiple_project_error(start: Path, matches: list[Path]) -> SessionError:
    candidates = ", ".join(str(path) for path in matches)
    return SessionError(
        f"multiple project manifests found from {start}: {candidates}; "
        "pass --project explicitly"
    )


def discover_project(start: str | Path = ".") -> Path:
    requested = Path(start).expanduser()
    if requested.is_file():
        manifest = requested.absolute()
        if manifest.name != "project.json":
            raise SessionError(f"project path must be project.json: {manifest}")
        return manifest
    candidate = requested.resolve()
    if not candidate.is_dir():
        raise SessionError(f"project path does not exist: {candidate}")

    direct = candidate / "project.json"
    if direct.is_file():
        return direct

    for parent in candidate.parents:
        ancestor = parent / "project.json"
        if ancestor.is_file():
            return ancestor

    descendants = _descendant_project_candidates(candidate)
    if len(descendants) == 1:
        return descendants[0]
    if len(descendants) > 1:
        raise _multiple_project_error(candidate, descendants)
    raise SessionError(
        f"project.json was not found from {candidate}; run `isekai init --path "
        f"{candidate}` or pass --project explicitly"
    )


def _unit_candidates(project_path: Path) -> list[Path]:
    units_root = project_path.parent / "units"
    try:
        root_metadata = units_root.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise SessionError(f"cannot inspect Unit root {units_root}: {exc}") from exc
    if metadata_is_path_alias(root_metadata) or not stat.S_ISDIR(
        root_metadata.st_mode
    ):
        return []
    candidates: list[Path] = []
    try:
        children = list(units_root.iterdir())
    except OSError as exc:
        raise SessionError(f"cannot list Unit root {units_root}: {exc}") from exc
    for path in children:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SessionError(f"cannot inspect Unit candidate {path}: {exc}") from exc
        if (
            stat.S_ISDIR(metadata.st_mode)
            and not metadata_is_path_alias(metadata)
            and not path.name.startswith(".")
        ):
            candidates.append(path)
    return sorted(candidates)


def discover_unit(project_path: Path, unit_dir: str | Path | None = None) -> Path | None:
    if unit_dir is not None:
        path = Path(unit_dir).expanduser().resolve()
        if not path.is_dir():
            raise SessionError(f"Unit directory does not exist: {path}")
        return path

    candidates = _unit_candidates(project_path)
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise SessionError(f"multiple Units found ({names}); pass --unit explicitly")
    return candidates[0] if candidates else None


def _unit_ref(path: Path, status: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(path),
        "unit_id": status.get("unit_id"),
        "title": status.get("title"),
        "document_language": status.get("document_language"),
        "phase": status.get("phase"),
        "status": status.get("status"),
        "foundation_version": status.get("foundation_version"),
        "foundation_digest": status.get("foundation_digest"),
        "valid": status.get("valid"),
        "missing": status.get("missing", []),
        "issues": status.get("issues", []),
        "pending": status.get("pending", []),
        "blocked_by": status.get("blocked_by", []),
        "decision_count": status.get("decision_count", 0),
        "human_gate": status.get("human_gate"),
    }


def _unit_candidate_ref(path: Path) -> dict[str, Any]:
    try:
        unit = _unit_json(path, "unit.json")
    except ValueError as exc:
        return {
            "path": str(path),
            "unit_id": None,
            "title": None,
            "document_language": None,
            "status": None,
            "issue": str(exc),
        }
    return {
        "path": str(path),
        "unit_id": unit.get("id"),
        "title": unit.get("title"),
        "document_language": unit.get("document_language"),
        "status": unit.get("status"),
        "issue": None,
    }


def _adapter_mode(state: str) -> dict[str, Any]:
    if state not in {"on", "off"}:  # pragma: no cover - internal contract
        raise SessionError(f"unsupported adapter mode: {state}")
    return {
        "state": state,
        "default_state": "off",
        "scope": "conversation",
        "persistent": False,
        "automatic_routing": state == "on",
        "next_session_state": "off",
    }


def build_project_session(
    project: str | Path = ".",
    route: WorkRoute = WorkRoute.UNIT,
) -> dict[str, Any]:
    """Build Project context without selecting, validating, or resuming a Unit."""
    project_path = discover_project(project)
    context = resolve_context(project_path, route)
    unit_candidates = _unit_candidates(project_path)
    return {
        "project": {
            "manifest": str(project_path),
            "id": context["project_id"],
            "version": context["project_version"],
        },
        "context": context,
        "unit": None,
        "active_unit": None,
        "unit_candidates": [str(path) for path in unit_candidates],
        "unit_candidate_details": [
            _unit_candidate_ref(path) for path in unit_candidates
        ],
    }


def activate_session(project: str | Path = ".") -> dict[str, Any]:
    """Activate ISEKAI for one conversation without selecting a Unit."""
    return {
        **build_project_session(project),
        "adapter_mode": _adapter_mode("on"),
        "activation": "project",
    }


def deactivate_session() -> dict[str, Any]:
    """Describe conversation-local deactivation without reading or writing artifacts."""
    return {
        "adapter_mode": _adapter_mode("off"),
        "artifacts_changed": False,
        "checkpoint_changed": False,
        "next_action": "invoke on to activate ISEKAI in this conversation",
    }


def build_session(
    project: str | Path = ".",
    unit_dir: str | Path | None = None,
    route: WorkRoute = WorkRoute.UNIT,
) -> dict[str, Any]:
    session = build_project_session(project, route)
    project_path = Path(session["project"]["manifest"])
    selected_unit = discover_unit(project_path, unit_dir)
    unit = None
    if selected_unit is not None:
        status = unit_status(selected_unit)
        if status.get("project_id") != session["project"]["id"]:
            raise SessionError(
                f"Unit project_id does not match selected Project: {selected_unit}"
            )
        try:
            receipt = _unit_json(selected_unit, "context-receipt.json")
        except ValueError as exc:
            raise SessionError(str(exc)) from exc
        if receipt.get("receipt_id") != _context_receipt_id(receipt):
            raise SessionError(
                "Unit Context Receipt fingerprint does not match its bound context"
            )
        from .project_knowledge import (
            project_knowledge_binding_issues,
            project_knowledge_receipt_issues,
        )

        knowledge_issues = project_knowledge_receipt_issues(
            receipt.get("project_knowledge"),
            project_id=str(status.get("project_id")),
        )
        if knowledge_issues:
            raise SessionError(
                "Unit Context Receipt has invalid Project Knowledge: "
                + "; ".join(knowledge_issues)
            )
        knowledge_binding_issues = project_knowledge_binding_issues(
            selected_unit, receipt
        )
        if knowledge_binding_issues:
            raise SessionError(
                "Unit Context Receipt Project Knowledge binding is invalid: "
                + "; ".join(knowledge_binding_issues)
            )
        try:
            receipt_manifest = _receipt_source_manifest_path(
                receipt,
                unit_dir=selected_unit,
                selected_project=project_path,
            )
        except ValueError as exc:
            raise SessionError(str(exc)) from exc
        if receipt_manifest != project_path.resolve():
            raise SessionError(
                f"Unit source_manifest does not match selected Project: {selected_unit}"
            )
        context = session["context"]
        if status.get("foundation_version") != context.get("foundation_version"):
            raise SessionError(
                "Unit Foundation version does not match the selected Project; "
                "use a separate reviewed contract migration before resuming it"
            )
        if status.get("foundation_digest") != context.get("foundation_digest"):
            raise SessionError(
                "Unit Foundation contract digest does not match the selected Project; "
                "use a separate reviewed contract migration before resuming it"
            )
        changed_fields = _context_contract_changed_fields(receipt, context)
        if changed_fields:
            raise SessionError(
                "Unit Context Receipt does not match the selected Project fields: "
                + ", ".join(changed_fields)
                + "; unit-migrate only supports path relocation"
            )
        # Active work consumes its creation-time knowledge snapshot. The latest
        # Project release remains visible only when no Unit is selected.
        session["context"]["project_knowledge"] = copy.deepcopy(
            receipt.get("project_knowledge")
        )
        unit = _unit_ref(selected_unit, status)
    return {
        **session,
        "unit": unit,
        "active_unit": unit,
    }


def resume_session(project: str | Path = ".", unit_dir: str | Path | None = None) -> dict[str, Any]:
    session = build_session(project, unit_dir)
    if session["unit"] is None:
        raise SessionError("no Unit is available; run plugin unit-init first")

    selected = Path(session["unit"]["path"])
    try:
        checkpoint = _unit_json(selected, "checkpoint.json")
    except ValueError as exc:
        raise SessionError(str(exc)) from exc
    return {
        **session,
        "resume": {
            "completed": checkpoint.get("completed", []),
            "pending": checkpoint.get("pending", []),
            "blocked_by": checkpoint.get("blocked_by", []),
            "next_action": checkpoint.get("next_action"),
            "artifact_references": sorted(
                str(path.relative_to(selected))
                for path in selected.rglob("*")
                if path.is_file()
                and not path.is_symlink()
                and "__pycache__" not in path.parts
                and not path.name.startswith(UNIT_LOCK_NAME)
            ),
        },
    }


def migrate_unit_context(
    project: str | Path,
    unit_dir: str | Path,
) -> dict[str, Any]:
    """Rebind only path locators after a Project and its Unit have moved.

    Contract changes are intentionally rejected. Foundation, Profile, rule, and
    extension migrations require a separate reviewed workflow; this action only
    replaces machine-local filesystem locations with portable locators.
    """

    project_path = discover_project(project)
    selected_unit = discover_unit(project_path, unit_dir)
    if selected_unit is None:  # pragma: no cover - unit_dir is required by callers
        raise SessionError("Unit directory is required for context migration")
    context = resolve_context(project_path, WorkRoute.UNIT)
    with unit_lock(selected_unit):
        try:
            unit = _unit_json(selected_unit, "unit.json")
            receipt = _unit_json(selected_unit, "context-receipt.json")
        except ValueError as exc:
            raise SessionError(str(exc)) from exc
        if receipt.get("receipt_id") != _context_receipt_id(receipt):
            raise SessionError(
                "Unit Context Receipt fingerprint does not match its bound context"
            )
        if unit.get("project_id") != context.get("project_id"):
            raise SessionError("Unit project_id does not match selected Project")
        if unit.get("foundation_version") != context.get("foundation_version"):
            raise SessionError(
                "Unit Foundation version does not match the selected Project; "
                "path-only migration cannot change the Foundation contract"
            )
        if unit.get("foundation_digest") != context.get("foundation_digest"):
            raise SessionError(
                "Unit Foundation contract digest does not match the selected Project; "
                "path-only migration cannot change the Foundation contract"
            )

        changed_fields = _context_contract_changed_fields(receipt, context)
        if changed_fields:
            raise SessionError(
                "Unit Context Receipt has Project contract changes that path-only "
                "migration cannot apply: "
                + ", ".join(changed_fields)
            )

        migrated = _portable_context_receipt(
            context,
            project_root=project_path.parent,
            unit_dir=selected_unit,
        )
        if "project_knowledge" in receipt:
            migrated["project_knowledge"] = copy.deepcopy(
                receipt["project_knowledge"]
            )
        else:
            migrated.pop("project_knowledge", None)
        migrated["receipt_id"] = _context_receipt_id(migrated)
        previous_receipt_id = receipt.get("receipt_id")
        changed = migrated.get("receipt_id") != previous_receipt_id
        if changed:
            write_json_atomic(selected_unit / "context-receipt.json", migrated)
            persisted = _unit_json(selected_unit, "context-receipt.json")
            if persisted.get("receipt_id") != migrated.get("receipt_id"):
                raise SessionError("Unit Context Receipt migration postflight failed")
    return {
        "migrated": changed,
        "unit": str(selected_unit),
        "project": str(project_path),
        "previous_receipt_id": previous_receipt_id,
        "receipt_id": migrated["receipt_id"],
        "source_manifest": migrated["source_manifest"],
        "source_manifest_base": migrated["source_manifest_base"],
    }


def inception_session(project: str | Path = ".") -> dict[str, Any]:
    # Inception prepares a new Unit and therefore must not auto-select an existing
    # one. Projects commonly have several historical Units, while only resume and
    # status need the explicit Unit-selection contract.
    session = build_project_session(project, route=WorkRoute.UNIT)
    return {
        **session,
        "inception": {
            "decision_required": True,
            "questions": [
                "What problem and outcome should this Unit address?",
                "What is explicitly in scope and out of scope?",
                "What acceptance criteria make the outcome verifiable?",
                "What risks, sensitive data, remote effects, or other people are involved?",
            ],
            "required_artifacts": [
                "intent.md",
                "requirements.md",
                "decisions.json",
                "acceptance.md",
            ],
        },
    }


def update_checkpoint(
    unit_dir: str | Path,
    *,
    completed: list[str],
    pending: list[str],
    blocked_by: list[str],
    next_action: str,
) -> dict[str, Any]:
    path = Path(unit_dir).expanduser().resolve()
    if not path.is_dir():
        raise SessionError(f"Unit directory does not exist: {path}")
    if not isinstance(next_action, str) or not next_action.strip():
        raise SessionError("next_action must be a non-empty string")
    for field, values in (
        ("completed", completed),
        ("pending", pending),
        ("blocked_by", blocked_by),
    ):
        if not isinstance(values, list) or any(
            not isinstance(item, str) or not item.strip() for item in values
        ):
            raise SessionError(f"{field} must be a list of non-empty strings")
    with unit_lock(path):
        preflight_issues = _unit_preflight_issues(path)
        if preflight_issues:
            raise SessionError(
                "checkpoint update blocked: " + "; ".join(preflight_issues)
            )
        try:
            unit = _unit_json(path, "unit.json")
        except ValueError as exc:
            raise SessionError(str(exc)) from exc
        checkpoint = _checkpoint_record(unit, completed, pending, blocked_by, next_action)
        write_json_atomic(path / "checkpoint.json", checkpoint)
    return {"path": str(path / "checkpoint.json"), "checkpoint": checkpoint}


def _checkpoint_record(
    unit: dict[str, Any],
    completed: list[str],
    pending: list[str],
    blocked_by: list[str],
    next_action: str,
) -> dict[str, Any]:
    return {
        "unit_id": unit.get("id"),
        "completed": completed,
        "pending": pending,
        "blocked_by": blocked_by,
        "next_action": next_action,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
