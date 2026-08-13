from __future__ import annotations

"""Stable Foundation API backed by model, validation, evaluation, and promotion modules."""

from .evaluation import (
    as_release as _as_release,
    evaluate_case as _evaluate_case,
    evaluation_condition as _evaluation_condition,
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
    postflight_promotion as _postflight_promotion,
    preflight_promotion as _preflight_promotion,
    replace_staged as _replace_staged,
    restore_original as _restore_original,
    write_staged_json as _write_staged_json,
    plan_foundation_promotion,
    promote_foundation,
    record_foundation_decision,
    record_foundation_evidence,
)
from .release_validation import (
    approval_blockers as _approval_blockers,
    foundation_evidence_issues as _foundation_evidence_issues,
    latest_foundation_decision as _latest_foundation_decision,
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
    load_foundation_documents as _load_foundation_documents,
    load_json as _load_json,
    parse_timestamp as _parse_timestamp,
    safe_path as _safe_path,
    validate_asset_specific as _validate_asset_specific,
    validate_condition as _validate_condition,
    validate_cross_references as _validate_cross_references,
    validate_provenance as _validate_provenance,
    validate_provenance_record as _validate_provenance_record,
    validate_rule_metadata as _validate_rule_metadata,
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
