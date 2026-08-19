"""Handoff broker — reads Checkpoint + phase contract, builds handoff for next agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..support.files import read_control_file
from ..workflow.catalog import CATALOG_ROOT, load_catalog


def _read_json(path: Path) -> dict[str, Any]:
    content = read_control_file(path, root=path.parent, label=path.name)
    value: dict[str, Any] = json.loads(content.decode("utf-8"))
    return value


def _resolve_phase(work_dir: Path, entry: dict[str, Any]) -> str | None:
    phase_source = entry.get("phase_source")
    if not isinstance(phase_source, dict):
        return None
    src_file = phase_source.get("file")
    src_field = phase_source.get("field")
    if not isinstance(src_file, str) or not isinstance(src_field, str):
        return None
    file_path = work_dir / src_file
    if not file_path.is_file():
        return None
    try:
        data = _read_json(file_path)
        phase = data.get(src_field)
        return phase if isinstance(phase, str) else None
    except Exception:
        return None


def _active_entry_with_phases() -> dict[str, Any] | None:
    try:
        catalog = load_catalog()
    except Exception:
        return None
    for entry in catalog.get("entries", []):
        if isinstance(entry, dict) and entry.get("active") and entry.get("phases"):
            return entry
    return None


_AWAITING_DECISION_STATUSES = frozenset({
    "awaiting-inception-decision",
    "awaiting-release-decision",
})


def _is_awaiting_decision(status: object) -> bool:
    return isinstance(status, str) and status in _AWAITING_DECISION_STATUSES


def build_handoff(work_dir: str | Path) -> dict[str, Any] | None:
    work_dir = Path(work_dir).expanduser().resolve()
    entry = _active_entry_with_phases()
    if entry is None:
        return None

    phase = _resolve_phase(work_dir, entry)
    if phase is None:
        return None

    phases = entry.get("phases", {})
    contract = phases.get(phase, {})

    checkpoint: dict[str, Any] = {}
    checkpoint_path = work_dir / "checkpoint.json"
    if checkpoint_path.is_file():
        try:
            checkpoint = _read_json(checkpoint_path)
        except Exception:
            pass

    phase_source = entry.get("phase_source", {})
    status_file = phase_source.get("file", "unit.json")
    work_unit: dict[str, Any] = {}
    unit_path = work_dir / status_file
    if unit_path.is_file():
        try:
            work_unit = _read_json(unit_path)
        except Exception:
            pass

    skill_path: str | None = None
    skill_rel = contract.get("skill")
    if isinstance(skill_rel, str):
        skill_path = (CATALOG_ROOT / skill_rel).as_posix()

    return {
        "entry_id": entry.get("id"),
        "unit_id": work_unit.get("id"),
        "phase": phase,
        "status": work_unit.get("status"),
        "checkpoint": checkpoint,
        "stage_skill": skill_path,
        "allowed_actions": contract.get("allowed_actions", []),
        "checks": contract.get("checks", []),
        "next_action": checkpoint.get("next_action"),
        "completed": checkpoint.get("completed", []),
        "pending": checkpoint.get("pending", []),
        "blocked_by": checkpoint.get("blocked_by", []),
        "human_gate_pending": _is_awaiting_decision(work_unit.get("status")),
    }
