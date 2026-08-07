from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..support.scope import scope_pattern_matches
from .types import (
    EVALUATION_CLOCK,
    EVALUATOR_TYPES,
    FoundationError,
    FoundationRelease,
)
from .validation import (
    _parse_timestamp,
    _validate_condition,
    _validate_provenance,
    _validate_provenance_record,
    _validate_rule_metadata,
    load_foundation,
)


def _dict_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _not_expired(value: Any, now: datetime) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry > now


def _effective_and_not_expired(effective_from: Any, expires_at: Any, now: datetime) -> bool:
    try:
        effective = _parse_timestamp(effective_from, "effective_from")
        expiry = _parse_timestamp(expires_at, "expires_at")
    except FoundationError:
        return False
    return effective <= now < expiry


def evaluate_condition(condition: dict[str, Any], subject: dict[str, Any] | None = None, *, now: datetime | None = None) -> bool:
    """Evaluate a closed Foundation condition against a supplied evidence subject."""
    _validate_condition(condition, "condition")
    value = subject or {}
    current = now or datetime.now(timezone.utc)
    kind = condition["type"]
    if kind == "required-artifact":
        artifact = value.get("artifacts", {}).get(condition["artifact"], value.get(condition["artifact"]))
        passed = _dict_path(artifact, condition["field"]) == condition["equals"]
        if condition.get("commands_required"):
            passed = passed and bool(value.get("commands"))
        return passed
    if kind == "context-scope":
        return all(field in value for field in condition["required_fields"]) and (not condition.get("allowed_routes") or value.get("route") in condition["allowed_routes"])
    if kind == "extension-cannot-weaken-must":
        levels = {"MAY": 0, "SHOULD": 1, "MUST": 2}
        return levels.get(value.get("extension_level"), -1) >= levels["MUST"] and value.get("parent_level") == condition["parent_level"]
    if kind == "required-decision":
        return any(isinstance(d, dict) and d.get("id") == condition["decision_ref"] and d.get("gate") == condition["gate"] and d.get("outcome") == condition["outcome"] and d.get("decided_by") == condition["decided_by"] and d.get("scope") == condition["scope"] and (not d.get("expires_at") or _not_expired(d["expires_at"], current)) for d in value.get("decisions", []))
    if kind == "required-envelope":
        envelope = value.get("envelope")
        if not isinstance(envelope, dict) or envelope.get("status") != "approved":
            return False
        if not _not_expired(envelope.get("expires_at"), current) or not _not_expired(condition["expires_at"], current):
            return False
        action = value.get("action")
        target = value.get("target")
        stage = value.get("stage")
        scopes = envelope.get("scope")
        stages = envelope.get("stages")
        allowed = envelope.get("allowed_actions")
        forbidden = envelope.get("forbidden_actions")
        if not isinstance(action, str) or action != condition["action"] or not isinstance(allowed, list) or action not in allowed:
            return False
        if not isinstance(forbidden, list) or action in forbidden:
            return False
        if not isinstance(scopes, list) or not isinstance(target, str):
            return False
        in_scope = any(
            isinstance(item, str) and scope_pattern_matches(item, target)
            for item in scopes
        )
        if not in_scope or stage != condition["stage"]:
            return False
        if not isinstance(stages, list) or not any(isinstance(item, dict) and item.get("name") == stage and action in item.get("allowed_actions", []) for item in stages):
            return False
        return isinstance(envelope.get("max_iterations"), int) and not isinstance(envelope.get("max_iterations"), bool) and envelope["max_iterations"] > 0
    if kind == "required-lineage":
        return all(value.get(key) == condition[key] for key in ("mapping_ref", "source_ref", "target_ref", "transformation", "raw_reference"))
    if kind == "required-promotion-review":
        entry = value.get("entry")
        provenance = entry.get("provenance") if isinstance(entry, dict) else None
        evidence = value.get("evidence")
        review = value.get("review")
        decisions = value.get("decisions")
        if not isinstance(entry, dict) or entry.get("id") != condition["entry_ref"]:
            return False
        if not isinstance(provenance, dict):
            return False
        try:
            _validate_provenance_record(provenance, "knowledge entry provenance")
        except FoundationError:
            return False
        if entry.get("effective_from") != condition["effective_from"] or entry.get("expires_at") != condition["expires_at"]:
            return False
        if not _effective_and_not_expired(entry.get("effective_from"), entry.get("expires_at"), current):
            return False
        if not isinstance(evidence, list) or not evidence or any(not isinstance(item, dict) or item.get("id") not in condition["evidence_refs"] or item.get("passed") is not True for item in evidence):
            return False
        provided_evidence_refs = {
            item["id"] for item in evidence if isinstance(item, dict)
        }
        if not set(condition["evidence_refs"]) <= provided_evidence_refs:
            return False
        if not isinstance(review, dict) or review.get("entry_ref") != condition["entry_ref"] or review.get("reviewed_by") != condition["reviewed_by"] or review.get("duplicate_checked") is not True or review.get("evidence_ref") not in condition["evidence_refs"]:
            return False
        return any(isinstance(decision, dict) and decision.get("id") == condition["promotion_decision_ref"] and decision.get("outcome") == "approved" and decision.get("gate") == "knowledge" and _not_expired(decision.get("expires_at"), current) for decision in (decisions if isinstance(decisions, list) else []))
    if kind == "required-exception-controls":
        if not all(value.get(key) == condition[key] for key in ("rule_ref", "reason", "owner", "scope", "compensating_controls")) or not _not_expired(value.get("expires_at"), current):
            return False
        review = value.get("review")
        if not isinstance(review, dict) or review.get("id") != condition["review_ref"] or review.get("status") not in {"approved", "passed"}:
            return False
        return any(isinstance(decision, dict) and decision.get("id") == condition["decision_ref"] and decision.get("outcome") == "approved" and _not_expired(decision.get("expires_at"), current) for decision in value.get("decisions", []))
    if kind == "required-dod":
        return value.get("unit_ref") == condition["unit_ref"] and all(item in value.get("artifacts", []) for item in condition["required_artifacts"]) and all(item in value.get("evaluations", []) for item in condition["evaluation_refs"]) and value.get("evidence_ref") == condition["evidence_ref"] and value.get("evidence_passed") is True
    return False


def _as_release(root: str | Path | FoundationRelease) -> FoundationRelease:
    """Accept an already validated release so callers can avoid re-reading it."""
    if isinstance(root, FoundationRelease):
        return root
    return load_foundation(root)


def evaluate_routing_cases(root: str | Path | FoundationRelease) -> dict[str, Any]:
    foundation = _as_release(root)
    asset = foundation.assets.get("routing-evaluation")
    if asset is None:
        raise FoundationError("missing routing-evaluation asset")
    from ..workflow import RouteRequest, classify_work
    results = []
    for case in asset["content"]["cases"]:
        decision = classify_work(RouteRequest(**case["input"]))
        results.append({"id": case["id"], "expected": case["expected"], "actual": decision.route.value, "passed": decision.route.value == case["expected"]})
    return {"passed": all(item["passed"] for item in results), "cases": results}


def validate_foundation(root: str | Path) -> dict[str, Any]:
    foundation = load_foundation(root)
    routing = evaluate_routing_cases(foundation)
    return {"valid": True, "summary": foundation.summary(), "evaluations": {"routing": routing}}


# Public names for the structural validators that Project-owned assets reuse.
# Project extensions are held to the same rule, provenance, and condition
# contract as Foundation assets, so these are part of the supported surface.
validate_condition_definition = _validate_condition
validate_asset_provenance = _validate_provenance
validate_rule_definition = _validate_rule_metadata



def _evaluation_condition(foundation: FoundationRelease, asset: dict[str, Any]) -> dict[str, Any]:
    evaluator = asset["content"].get("evaluator")
    if evaluator not in EVALUATOR_TYPES:
        raise FoundationError(f"{asset['id']} has no supported evaluator")
    for reference in asset.get("extends", []):
        parent = foundation.assets.get(reference.get("id") if isinstance(reference, dict) else None)
        if parent is None:
            continue
        for rule in parent.get("content", {}).get("rules", []):
            condition = rule.get("condition") if isinstance(rule, dict) else None
            if isinstance(condition, dict) and condition.get("type") == evaluator:
                return condition
    raise FoundationError(f"{asset['id']} cannot resolve condition for evaluator {evaluator}")


def _evaluate_case(foundation: FoundationRelease, asset: dict[str, Any], case: dict[str, Any]) -> bool:
    evaluator = asset["content"].get("evaluator")
    subject = case["input"]
    if not isinstance(subject, dict):
        return False
    if evaluator == "required-decision":
        matrix = foundation.assets["gate-matrix"]["content"]["gates"]
        gate = subject.get("gate")
        if not any(item.get("id") == gate for item in matrix):
            return False
    condition = case.get("condition", _evaluation_condition(foundation, asset))
    return evaluate_condition(condition, subject, now=EVALUATION_CLOCK)


def evaluate_all_evaluations(root: str | Path | FoundationRelease) -> dict[str, Any]:
    """Run every versioned evaluation fixture through its declared evaluator."""
    foundation = _as_release(root)
    evaluations: dict[str, Any] = {"routing": evaluate_routing_cases(foundation)}
    for asset in sorted(foundation.assets_by_kind("evaluation"), key=lambda item: item["id"]):
        if asset["id"] == "routing-evaluation":
            continue
        cases = []
        for case in asset["content"]["cases"]:
            expected = case["expected"]
            actual = "pass" if _evaluate_case(foundation, asset, case) else "fail"
            cases.append({"id": case["id"], "expected": expected, "actual": actual, "passed": actual == expected})
        evaluations[asset["id"]] = {"passed": all(item["passed"] for item in cases), "cases": cases}
    return {"passed": all(result["passed"] for result in evaluations.values()), "evaluations": evaluations}


def evaluate_foundation(root: str | Path) -> dict[str, Any]:
    """Validate the Foundation and execute its complete evaluation matrix."""
    foundation = load_foundation(root)
    evaluations = evaluate_all_evaluations(foundation)
    return {"valid": True, "evaluations_passed": evaluations["passed"], "summary": foundation.summary(), "evaluations": evaluations["evaluations"]}
