"""Compatibility façade for project context operations."""

from .workflow.project import (
    _load_json,
    _load_project_extension,
    initialize_project,
    load_project,
    resolve_context,
)

__all__ = ["initialize_project", "load_project", "resolve_context"]
