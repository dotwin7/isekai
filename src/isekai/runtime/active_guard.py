from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..workflow.active_binding import (
    active_unit_binding,
    project_manifest_for_unit,
    require_active_unit_match,
)
from ..workflow.session import discover_project
from .request_fields import RuntimeContractError, string_field


UNIT_BOUND_ACTIONS = {
    "unit-migrate",
    "checkpoint",
    "amend",
    "envelope-propose",
    "envelope-approve",
    "authorize",
    "managed-edit",
    "artifact-write",
    "prove",
    "evidence",
    "decision",
    "transition",
    "project-knowledge-propose",
    "project-knowledge-promote",
}
_READ_ONLY_UNIT_ACTIONS = {"verify"}
_PROJECT_ROUTE_ACTIONS = {"inception", "route"}


def guard_active_unit(action: str, values: Mapping[str, Any]) -> None:
    if action in UNIT_BOUND_ACTIONS | _READ_ONLY_UNIT_ACTIONS:
        unit = Path(string_field(values, "unit")).expanduser().resolve()
        if not unit.is_dir() or not (unit / "unit.json").is_file():
            return  # The action handler reports its own payload/path error.
        project = project_manifest_for_unit(unit)
        require_active_unit_match(
            project,
            unit,
            action=action,
            bind_if_empty=action in UNIT_BOUND_ACTIONS,
        )
    elif action in _PROJECT_ROUTE_ACTIONS:
        project = discover_project(string_field(values, "project", default="."))
        binding = active_unit_binding(project)
        if binding.get("active"):
            active = binding.get("unit") or {}
            raise RuntimeContractError(
                f"{action} blocked by unfinished active Unit {active.get('unit_id')}; "
                "record the request with amend"
            )
