from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..support.jsonio import write_json_atomic
from .project import resolve_context
from .routing import WorkRoute
from .unit.common import UNIT_LOCK_NAME, unit_lock
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
            matches.append(Path(current, "project.json").resolve())
    return sorted(set(matches))


def _multiple_project_error(start: Path, matches: list[Path]) -> SessionError:
    candidates = ", ".join(str(path) for path in matches)
    return SessionError(
        f"multiple project manifests found from {start}: {candidates}; "
        "pass --project explicitly"
    )


def discover_project(start: str | Path = ".") -> Path:
    candidate = Path(start).expanduser().resolve()
    if candidate.is_file():
        if candidate.name != "project.json":
            raise SessionError(f"project path must be project.json: {candidate}")
        return candidate
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
    if not units_root.is_dir():
        return []
    return sorted(
        path for path in units_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


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


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SessionError(f"missing JSON artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SessionError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SessionError(f"JSON artifact must be an object: {path}")
    return value


def _unit_ref(path: Path, status: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(path),
        "unit_id": status.get("unit_id"),
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
    return {
        "project": {
            "manifest": str(project_path),
            "id": context["project_id"],
            "version": context["project_version"],
        },
        "context": context,
        "unit": None,
        "active_unit": None,
        "unit_candidates": [str(path) for path in _unit_candidates(project_path)],
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
        receipt = _read_object(selected_unit / "context-receipt.json")
        source_manifest = receipt.get("source_manifest")
        if (
            not isinstance(source_manifest, str)
            or not source_manifest.strip()
            or Path(source_manifest).expanduser().resolve() != project_path.resolve()
        ):
            raise SessionError(
                f"Unit source_manifest does not match selected Project: {selected_unit}"
            )
        context = session["context"]
        if status.get("foundation_version") != context.get("foundation_version"):
            raise SessionError(
                "Unit Foundation version does not match the selected Project; "
                "migrate the Unit explicitly before resuming it"
            )
        if status.get("foundation_digest") != context.get("foundation_digest"):
            raise SessionError(
                "Unit Foundation contract digest does not match the selected Project; "
                "migrate the Unit explicitly before resuming it"
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
    checkpoint = _read_object(selected / "checkpoint.json")
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
                and "__pycache__" not in path.parts
                and not path.name.startswith(UNIT_LOCK_NAME)
            ),
        },
    }


def inception_session(project: str | Path = ".") -> dict[str, Any]:
    session = build_session(project, route=WorkRoute.UNIT)
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
        unit = _read_object(path / "unit.json")
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
