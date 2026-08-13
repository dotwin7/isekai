"""Backward-compatible facade that re-exports from platform and AI-DLC modules.

Platform workflow code (project, catalog, active_binding, project_knowledge,
session, errors) lives in this package.  AI-DLC catalog entry code (intake, routing,
unit/) has moved to ``isekai.catalog.ai_dlc``.  This facade re-exports both
so that existing callers continue to work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .project import initialize_project as _initialize_project
from .project import load_project, resolve_context
from .catalog import (
    catalog_resources,
    load_catalog,
    read_catalog_resource,
)
from isekai.support.errors import (
    AuthorizationError,
    EvidenceError,
    IntegrityError,
    LifecycleError,
    PreflightError,
    WorkflowError,
)


def initialize_project(
    path: str | Path = ".",
    *,
    project_id: str | None = None,
    foundation_path: str | None = None,
    profiles: list[str] | None = None,
    document_language: str = "ko",
    maximum_agent_level: str = "L0",
) -> Path:
    """Initialize through the stable facade and preserve postflight injection."""
    return _initialize_project(
        path,
        project_id=project_id,
        foundation_path=foundation_path,
        profiles=profiles,
        document_language=document_language,
        maximum_agent_level=maximum_agent_level,
        _postflight=load_project,
    )


def __getattr__(name: str) -> Any:
    """Lazy re-export of AI-DLC symbols that callers previously found here."""
    from .authorization import authorize_action as _authorize_action
    from .project_knowledge import (
        current_project_knowledge as _current_project_knowledge,
        project_knowledge_status as _project_knowledge_status,
        promote_project_knowledge as _promote_project_knowledge,
        propose_project_knowledge as _propose_project_knowledge,
        select_project_knowledge_context as _select_project_knowledge_context,
        summarize_project_knowledge as _summarize_project_knowledge,
    )
    from isekai.catalog.ai_dlc.routing import (
        AGENT_ALLOWED_ACTIONS as _AGENT_ALLOWED_ACTIONS,
        AGENT_LEVEL_ALLOWED_ACTIONS as _AGENT_LEVEL_ALLOWED_ACTIONS,
        AGENT_PROHIBITED_ACTIONS as _AGENT_PROHIBITED_ACTIONS,
        ALLOWED_AGENT_LEVELS as _ALLOWED_AGENT_LEVELS,
        RouteDecision as _RouteDecision,
        RouteRequest as _RouteRequest,
        WorkRoute as _WorkRoute,
        classify_work as _classify_work,
    )
    from isekai.catalog.ai_dlc.unit.authorization import (
        AUTHORIZATION_GRANT_REQUIRED_FIELDS as _AUTHORIZATION_GRANT_REQUIRED_FIELDS,
        AUTHORIZATION_LEDGER_REQUIRED_FIELDS as _AUTHORIZATION_LEDGER_REQUIRED_FIELDS,
        authorization_ledger_issues as _authorization_ledger_issues,
    )
    from isekai.catalog.ai_dlc.unit.amendments import (
        record_unit_amendment as _record_unit_amendment,
    )
    from isekai.catalog.ai_dlc.unit.common import (
        PROTECTED_UNIT_ARTIFACTS as _PROTECTED_UNIT_ARTIFACTS,
        UNIT_LOCK_NAME as _UNIT_LOCK_NAME,
        UNIT_REQUIRED_FILES as _UNIT_REQUIRED_FILES,
        is_iso_timestamp as _is_iso_timestamp,
        unit_json as _unit_json,
        unit_preflight_issues as _unit_preflight_issues,
        unit_lock as _unit_lock,
    )
    from isekai.catalog.ai_dlc.unit.decisions import (
        approved_envelope_decision_issues as _approved_envelope_decision_issues,
        record_decision as _record_decision,
    )
    from isekai.catalog.ai_dlc.unit.decision_schema import (
        ALLOWED_TRANSITIONS as _ALLOWED_TRANSITIONS,
        DECISION_GATES as _DECISION_GATES,
        DECISION_OUTCOMES as _DECISION_OUTCOMES,
        DECISION_PACKET_VERSION as _DECISION_PACKET_VERSION,
        DECISION_REQUIRED_FIELDS as _DECISION_REQUIRED_FIELDS,
        LIFECYCLE_STATUSES as _LIFECYCLE_STATUSES,
        REQUIRED_DECISIONS_FOR_TRANSITIONS as _REQUIRED_DECISIONS_FOR_TRANSITIONS,
        STATUS_PHASE as _STATUS_PHASE,
        decision_packet_issues as _decision_packet_issues,
        decision_record_digest as _decision_record_digest,
        decision_record_issues as _decision_record_issues,
        has_approved_decision as _has_approved_decision,
        latest_decision as _latest_decision,
    )
    from isekai.catalog.ai_dlc.unit.evidence import (
        passing_evidence as _passing_evidence,
        build_command_evidence as _build_command_evidence,
        record_evidence as _record_evidence,
        validate_evidence as _evidence_issues,
    )
    from isekai.catalog.ai_dlc.unit.execution import (
        EXECUTION_ENVELOPE_DEFAULT_HOURS as _EXECUTION_ENVELOPE_DEFAULT_HOURS,
        EXECUTION_ENVELOPE_MAX_HOURS as _EXECUTION_ENVELOPE_MAX_HOURS,
        EXECUTION_ENVELOPE_REQUIRED_FIELDS as _EXECUTION_ENVELOPE_REQUIRED_FIELDS,
        EXECUTION_ENVELOPE_STATUSES as _EXECUTION_ENVELOPE_STATUSES,
        approve_execution_envelope_locked as _approve_execution_envelope,
        approve_execution_envelope as _approve_execution_envelope_func,
        propose_execution_envelope as _propose_execution_envelope,
    )
    from isekai.catalog.ai_dlc.unit.execution_schema import (
        execution_envelope_approval_digest as _execution_envelope_approval_digest,
        execution_envelope_issues as _execution_envelope_issues,
    )
    from isekai.catalog.ai_dlc.unit.initialization import (
        initialize_unit as _initialize_unit,
    )
    from isekai.catalog.ai_dlc.unit.lifecycle import (
        transition_unit as _transition_unit,
        unit_status as _unit_status,
        verify_unit as _verify_unit,
    )

    _lazy = {
        "AGENT_ALLOWED_ACTIONS": _AGENT_ALLOWED_ACTIONS,
        "AGENT_LEVEL_ALLOWED_ACTIONS": _AGENT_LEVEL_ALLOWED_ACTIONS,
        "AGENT_PROHIBITED_ACTIONS": _AGENT_PROHIBITED_ACTIONS,
        "ALLOWED_AGENT_LEVELS": _ALLOWED_AGENT_LEVELS,
        "ALLOWED_TRANSITIONS": _ALLOWED_TRANSITIONS,
        "AUTHORIZATION_GRANT_REQUIRED_FIELDS": _AUTHORIZATION_GRANT_REQUIRED_FIELDS,
        "AUTHORIZATION_LEDGER_REQUIRED_FIELDS": _AUTHORIZATION_LEDGER_REQUIRED_FIELDS,
        "DECISION_GATES": _DECISION_GATES,
        "DECISION_OUTCOMES": _DECISION_OUTCOMES,
        "DECISION_PACKET_VERSION": _DECISION_PACKET_VERSION,
        "DECISION_REQUIRED_FIELDS": _DECISION_REQUIRED_FIELDS,
        "EXECUTION_ENVELOPE_DEFAULT_HOURS": _EXECUTION_ENVELOPE_DEFAULT_HOURS,
        "EXECUTION_ENVELOPE_MAX_HOURS": _EXECUTION_ENVELOPE_MAX_HOURS,
        "EXECUTION_ENVELOPE_REQUIRED_FIELDS": _EXECUTION_ENVELOPE_REQUIRED_FIELDS,
        "EXECUTION_ENVELOPE_STATUSES": _EXECUTION_ENVELOPE_STATUSES,
        "LIFECYCLE_STATUSES": _LIFECYCLE_STATUSES,
        "PROTECTED_UNIT_ARTIFACTS": _PROTECTED_UNIT_ARTIFACTS,
        "REQUIRED_DECISIONS_FOR_TRANSITIONS": _REQUIRED_DECISIONS_FOR_TRANSITIONS,
        "RouteDecision": _RouteDecision,
        "RouteRequest": _RouteRequest,
        "STATUS_PHASE": _STATUS_PHASE,
        "UNIT_LOCK_NAME": _UNIT_LOCK_NAME,
        "UNIT_REQUIRED_FILES": _UNIT_REQUIRED_FILES,
        "WorkRoute": _WorkRoute,
        "_approve_execution_envelope": _approve_execution_envelope,
        "_approved_envelope_decision_issues": _approved_envelope_decision_issues,
        "_authorization_ledger_issues": _authorization_ledger_issues,
        "_decision_packet_issues": _decision_packet_issues,
        "_decision_record_digest": _decision_record_digest,
        "_decision_record_issues": _decision_record_issues,
        "_evidence_issues": _evidence_issues,
        "_execution_envelope_approval_digest": _execution_envelope_approval_digest,
        "_execution_envelope_issues": _execution_envelope_issues,
        "_has_approved_decision": _has_approved_decision,
        "_is_iso_timestamp": _is_iso_timestamp,
        "_latest_decision": _latest_decision,
        "_passing_evidence": _passing_evidence,
        "_unit_json": _unit_json,
        "_unit_preflight_issues": _unit_preflight_issues,
        "approve_execution_envelope": _approve_execution_envelope_func,
        "authorize_action": _authorize_action,
        "build_command_evidence": _build_command_evidence,
        "classify_work": _classify_work,
        "current_project_knowledge": _current_project_knowledge,
        "initialize_unit": _initialize_unit,
        "propose_execution_envelope": _propose_execution_envelope,
        "project_knowledge_status": _project_knowledge_status,
        "promote_project_knowledge": _promote_project_knowledge,
        "propose_project_knowledge": _propose_project_knowledge,
        "record_decision": _record_decision,
        "record_evidence": _record_evidence,
        "record_unit_amendment": _record_unit_amendment,
        "select_project_knowledge_context": _select_project_knowledge_context,
        "summarize_project_knowledge": _summarize_project_knowledge,
        "transition_unit": _transition_unit,
        "unit_lock": _unit_lock,
        "unit_status": _unit_status,
        "verify_unit": _verify_unit,
    }
    if name in _lazy:
        return _lazy[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AGENT_LEVEL_ALLOWED_ACTIONS",
    "AuthorizationError",
    "EvidenceError",
    "IntegrityError",
    "LifecycleError",
    "PreflightError",
    "RouteDecision",
    "RouteRequest",
    "WorkRoute",
    "WorkflowError",
    "approve_execution_envelope",
    "authorize_action",
    "build_command_evidence",
    "classify_work",
    "current_project_knowledge",
    "catalog_resources",
    "initialize_project",
    "initialize_unit",
    "load_catalog",
    "load_project",
    "project_knowledge_status",
    "promote_project_knowledge",
    "propose_execution_envelope",
    "propose_project_knowledge",
    "read_catalog_resource",
    "record_decision",
    "record_evidence",
    "record_unit_amendment",
    "resolve_context",
    "select_project_knowledge_context",
    "summarize_project_knowledge",
    "transition_unit",
    "unit_lock",
    "unit_status",
    "verify_unit",
]
