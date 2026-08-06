"""Compatibility façade for project and Unit session handling."""

from .workflow.session import (
    PROJECT_DISCOVERY_EXCLUDES,
    SessionError,
    _adapter_mode,
    _checkpoint_record,
    _descendant_project_candidates,
    _multiple_project_error,
    _read_object,
    _unit_candidates,
    _unit_ref,
    activate_session,
    build_project_session,
    build_session,
    deactivate_session,
    discover_project,
    discover_unit,
    inception_session,
    resume_session,
    update_checkpoint,
)

__all__ = [
    "SessionError",
    "activate_session",
    "build_project_session",
    "build_session",
    "deactivate_session",
    "discover_project",
    "discover_unit",
    "inception_session",
    "resume_session",
    "update_checkpoint",
]
