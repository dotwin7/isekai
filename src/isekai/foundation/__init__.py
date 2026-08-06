from __future__ import annotations

"""Stable Foundation API backed by model, validation, evaluation, and promotion modules."""

from .evaluation import (
    _as_release,
    _evaluate_case,
    _evaluation_condition,
    evaluate_all_evaluations,
    evaluate_condition,
    evaluate_foundation,
    evaluate_routing_cases,
    validate_asset_provenance,
    validate_condition_definition,
    validate_foundation,
    validate_rule_definition,
)
from .promotion import (
    _approval_blockers,
    _foundation_evidence_issues,
    _latest_foundation_decision,
    _postflight_promotion,
    _preflight_promotion,
    _replace_staged,
    _restore_original,
    _write_staged_json,
    plan_foundation_promotion,
    promote_foundation,
    record_foundation_decision,
    record_foundation_evidence,
)
from .types import (
    ALLOWED_ASSET_KINDS,
    ALLOWED_STATUSES,
    CONDITION_TYPES,
    EVALUATION_CLOCK,
    EVALUATOR_TYPES,
    FOUNDATION_CHECK_FIELDS,
    FOUNDATION_DECISION_FIELDS,
    FOUNDATION_EVIDENCE_FIELDS,
    FOUNDATION_LOCK_NAME,
    REQUIRED_ASSET_FIELDS,
    FoundationError,
    FoundationRelease,
)
from .validation import (
    _load_foundation_documents,
    _load_json,
    _parse_timestamp,
    _safe_path,
    _validate_asset_specific,
    _validate_condition,
    _validate_cross_references,
    _validate_provenance,
    _validate_provenance_record,
    _validate_rule_metadata,
    load_foundation,
    validate_context,
)


__all__ = [
    "FoundationError",
    "FoundationRelease",
    "evaluate_all_evaluations",
    "evaluate_condition",
    "evaluate_foundation",
    "evaluate_routing_cases",
    "load_foundation",
    "plan_foundation_promotion",
    "promote_foundation",
    "record_foundation_decision",
    "record_foundation_evidence",
    "validate_asset_provenance",
    "validate_condition_definition",
    "validate_context",
    "validate_foundation",
    "validate_rule_definition",
]
