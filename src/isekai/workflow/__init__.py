from __future__ import annotations

"""Stable workflow API backed by focused domain modules.

The plugin, CLI, and downstream callers keep importing ``isekai.workflow``.
Implementation lives in smaller modules so each lifecycle concern can evolve
without recreating a monolithic workflow module.
"""

from .project import initialize_project as _initialize_project
from .project import load_project, resolve_context
from .routing import (
    AGENT_ALLOWED_ACTIONS,
    AGENT_PROHIBITED_ACTIONS,
    ALLOWED_AGENT_LEVELS,
    RouteDecision,
    RouteRequest,
    WorkRoute,
    classify_work,
)
from .unit.authorization import (
    AUTHORIZATION_GRANT_REQUIRED_FIELDS,
    AUTHORIZATION_LEDGER_REQUIRED_FIELDS,
    _authorization_ledger_issues,
)
from .unit.common import (
    PROTECTED_UNIT_ARTIFACTS,
    UNIT_LOCK_NAME,
    UNIT_REQUIRED_FILES,
    _unit_json,
    _unit_preflight_issues,
    unit_lock,
)
from .unit.decisions import (
    ALLOWED_TRANSITIONS,
    DECISION_GATES,
    DECISION_OUTCOMES,
    DECISION_PACKET_VERSION,
    DECISION_REQUIRED_FIELDS,
    LIFECYCLE_STATUSES,
    REQUIRED_DECISIONS_FOR_TRANSITIONS,
    STATUS_PHASE,
    _approved_envelope_decision_issues,
    _decision_packet_issues,
    _decision_record_digest,
    _decision_record_issues,
    _has_approved_decision,
    _is_iso_timestamp,
    _latest_decision,
    record_decision,
)
from .unit.evidence import (
    _evidence_issues,
    _passing_evidence,
    build_command_evidence,
    record_evidence,
)
from .unit.execution import (
    EXECUTION_ENVELOPE_DEFAULT_HOURS,
    EXECUTION_ENVELOPE_MAX_HOURS,
    EXECUTION_ENVELOPE_REQUIRED_FIELDS,
    EXECUTION_ENVELOPE_STATUSES,
    _approve_execution_envelope,
    _execution_envelope_approval_digest,
    _execution_envelope_issues,
    approve_execution_envelope,
    authorize_action,
    propose_execution_envelope,
)
from .unit.initialization import initialize_unit
from .unit.lifecycle import transition_unit, unit_status, verify_unit


def initialize_project(
    path=".",
    *,
    project_id=None,
    foundation_path=None,
    profiles=None,
    document_language="ko",
    maximum_agent_level="L0",
):
    """Initialize through the stable façade and preserve postflight injection."""
    return _initialize_project(
        path,
        project_id=project_id,
        foundation_path=foundation_path,
        profiles=profiles,
        document_language=document_language,
        maximum_agent_level=maximum_agent_level,
        _postflight=load_project,
    )


__all__ = [
    "RouteDecision",
    "RouteRequest",
    "WorkRoute",
    "approve_execution_envelope",
    "authorize_action",
    "build_command_evidence",
    "classify_work",
    "initialize_project",
    "initialize_unit",
    "load_project",
    "propose_execution_envelope",
    "record_decision",
    "record_evidence",
    "resolve_context",
    "transition_unit",
    "unit_lock",
    "unit_status",
    "verify_unit",
]
