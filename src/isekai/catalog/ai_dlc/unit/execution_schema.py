from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from isekai.catalog.ai_dlc.routing import (
    AGENT_ALLOWED_ACTIONS,
    AGENT_LEVEL_ALLOWED_ACTIONS,
    AGENT_PROHIBITED_ACTIONS,
)
from isekai.support.scope import scope_pattern_matches
from .external_access import EXTERNAL_API_ACTION, external_access_policy_issues


EXECUTION_ENVELOPE_APPROVAL_FIELDS = {
    "id",
    "type",
    "schema_version",
    "unit_id",
    "scope",
    "stages",
    "allowed_actions",
    "forbidden_actions",
    "external_access",
    "max_iterations",
    "proposed_by",
    "proposed_at",
    "expires_at",
}


def execution_envelope_approval_digest(envelope: dict[str, Any]) -> str:
    subject = {
        field: envelope.get(field)
        for field in sorted(EXECUTION_ENVELOPE_APPROVAL_FIELDS)
        if field != "external_access" or field in envelope
    }
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


# Backward-compatible name for callers that still use the pre-contract facade.
_execution_envelope_approval_digest = execution_envelope_approval_digest


def _scope_pattern_issue(pattern: str) -> str | None:
    normalized = pattern.replace("\\", "/")
    if (
        not normalized.strip()
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or ".." in normalized.split("/")
    ):
        return f"Execution Envelope scope must be a project-relative pattern: {pattern}"
    return None


def _scope_pattern_matches(pattern: str, target: str) -> bool:
    """Match a scope pattern without allowing wildcards to cross segments."""
    return scope_pattern_matches(pattern, target)

EXECUTION_ENVELOPE_REQUIRED_FIELDS = {
    "id",
    "type",
    "schema_version",
    "unit_id",
    "status",
    "scope",
    "stages",
    "allowed_actions",
    "forbidden_actions",
    "max_iterations",
    "proposed_by",
    "proposed_at",
    "expires_at",
    "approval_digest",
}
EXECUTION_ENVELOPE_STATUSES = {"proposed", "approved"}
EXECUTION_STAGE_DEPTHS = {"light", "standard", "deep"}
EXECUTION_STAGE_DISPOSITIONS = {"apply", "skip"}
EXECUTION_ENVELOPE_DEFAULT_HOURS = 168
EXECUTION_ENVELOPE_MAX_HOURS = 720
EXECUTION_ENVELOPE_PROPOSABLE_STATUSES = {
    "proposed",
    "inception",
    "awaiting-inception-decision",
    "construction",
    "validation",
    "awaiting-release-decision",
    "releasing",
    "operating",
}


def _envelope_identity_issues(
    envelope: dict[str, Any],
    unit_id: str | None,
    *,
    require_approved: bool,
    check_expiry: bool,
) -> list[str]:
    issues: list[str] = []
    missing = sorted(EXECUTION_ENVELOPE_REQUIRED_FIELDS - envelope.keys())
    if missing:
        issues.append(f"Execution Envelope missing fields: {', '.join(missing)}")
    if envelope.get("type") != "execution-envelope":
        issues.append("Execution Envelope has an invalid type")
    if envelope.get("schema_version") != "1.0.0":
        issues.append("Execution Envelope has an unsupported schema_version")
    if unit_id is not None and envelope.get("unit_id") != unit_id:
        issues.append("Execution Envelope unit_id does not match Unit")
    status = envelope.get("status")
    if not isinstance(status, str) or status not in EXECUTION_ENVELOPE_STATUSES:
        issues.append("Execution Envelope has an invalid status")
    approval_digest = envelope.get("approval_digest")
    if not isinstance(approval_digest, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", approval_digest
    ):
        issues.append("Execution Envelope approval_digest must be a SHA-256 digest")
    elif approval_digest != execution_envelope_approval_digest(envelope):
        issues.append("Execution Envelope approval_digest does not match its approval subject")
    expires_at = envelope.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at.strip():
        issues.append("Execution Envelope requires expires_at")
    else:
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if check_expiry and expiry <= datetime.now(timezone.utc):
                issues.append(
                    "Execution Envelope is expired; propose a new Envelope and "
                    "record a new approved inception Decision to renew it"
                )
        except ValueError:
            issues.append("Execution Envelope expires_at must be an ISO-8601 timestamp")
    if require_approved and envelope.get("status") != "approved":
        issues.append("Execution Envelope is not approved")
    return issues


scope_pattern_issue = _scope_pattern_issue
envelope_scope_pattern_matches = _scope_pattern_matches


def _envelope_action_issues(
    envelope: dict[str, Any],
    maximum_agent_level: str | None,
) -> list[str]:
    issues: list[str] = []
    for field in ("scope", "allowed_actions", "forbidden_actions"):
        value = envelope.get(field)
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            issues.append(f"Execution Envelope {field} must be a list of strings")
        elif field in {"scope", "allowed_actions"} and not value:
            issues.append(f"Execution Envelope {field} must not be empty")
        if field == "scope" and isinstance(value, list):
            issues.extend(
                issue
                for item in value
                if isinstance(item, str)
                for issue in [_scope_pattern_issue(item)]
                if issue is not None
            )
    allowed_actions = envelope.get("allowed_actions")
    forbidden_actions = envelope.get("forbidden_actions")
    external_access = envelope.get("external_access", [])
    issues.extend(external_access_policy_issues(external_access))
    if isinstance(allowed_actions, list) and all(
        isinstance(item, str) for item in allowed_actions
    ):
        unknown_actions = sorted(set(allowed_actions) - AGENT_ALLOWED_ACTIONS)
        if unknown_actions:
            issues.append(
                "Execution Envelope contains unsupported allowed actions: "
                + ", ".join(unknown_actions)
            )
        prohibited_actions = sorted(set(allowed_actions) & AGENT_PROHIBITED_ACTIONS)
        if prohibited_actions:
            issues.append(
                "Execution Envelope cannot allow prohibited actions: "
                + ", ".join(prohibited_actions)
            )
        if maximum_agent_level is not None:
            level_actions = AGENT_LEVEL_ALLOWED_ACTIONS.get(maximum_agent_level)
            if level_actions is None:
                issues.append(
                    "Execution Envelope has an unsupported maximum_agent_level: "
                    + maximum_agent_level
                )
            else:
                above_level = sorted(set(allowed_actions) - level_actions)
                if above_level:
                    issues.append(
                        "Execution Envelope actions exceed Project "
                        f"maximum_agent_level {maximum_agent_level}: "
                        + ", ".join(above_level)
                    )
        if isinstance(forbidden_actions, list) and all(
            isinstance(item, str) for item in forbidden_actions
        ):
            overlap = sorted(set(allowed_actions) & set(forbidden_actions))
            if overlap:
                issues.append(
                    "Execution Envelope actions cannot be both allowed and forbidden: "
                    + ", ".join(overlap)
                )
        if EXTERNAL_API_ACTION in allowed_actions:
            if not isinstance(external_access, list) or not external_access:
                issues.append(
                    "Execution Envelope external-api action requires external_access"
                )
        elif isinstance(external_access, list) and external_access:
            issues.append(
                "Execution Envelope external_access requires external-api in allowed_actions"
            )
    max_iterations = envelope.get("max_iterations")
    if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations <= 0:
        issues.append("Execution Envelope max_iterations must be a positive integer")
    return issues


def _envelope_stage_issues(envelope: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    allowed_actions = envelope.get("allowed_actions")
    stages = envelope.get("stages")
    if not isinstance(stages, list) or not stages:
        issues.append("Execution Envelope must define at least one stage")
    else:
        seen_stage_names: set[str] = set()
        for index, stage in enumerate(stages):
            if not isinstance(stage, dict):
                issues.append(f"Execution Envelope stage {index} must be an object")
                continue
            if not isinstance(stage.get("name"), str) or not stage["name"].strip():
                issues.append(f"Execution Envelope stage {index} needs name")
            elif stage["name"] in seen_stage_names:
                issues.append(f"Execution Envelope has duplicate stage: {stage['name']}")
            else:
                seen_stage_names.add(stage["name"])
            depth = stage.get("depth")
            if not isinstance(depth, str) or depth not in EXECUTION_STAGE_DEPTHS:
                issues.append(
                    f"Execution Envelope stage {index} depth must be one of: "
                    + ", ".join(sorted(EXECUTION_STAGE_DEPTHS))
                )
            disposition = stage.get("disposition")
            if disposition is not None:
                if not isinstance(disposition, str) or disposition not in (
                    EXECUTION_STAGE_DISPOSITIONS
                ):
                    issues.append(
                        f"Execution Envelope stage {index} disposition must be one of: "
                        + ", ".join(sorted(EXECUTION_STAGE_DISPOSITIONS))
                    )
                if not isinstance(stage.get("reason"), str) or not stage["reason"].strip():
                    issues.append(
                        f"Execution Envelope stage {index} with a disposition needs reason"
                    )
            actions = stage.get("allowed_actions")
            if not isinstance(actions, list) or any(
                not isinstance(item, str) or not item.strip() for item in actions
            ):
                issues.append(
                    f"Execution Envelope stage {index} allowed_actions must be a list of strings"
                )
            else:
                unknown_stage_actions = sorted(set(actions) - AGENT_ALLOWED_ACTIONS)
                if unknown_stage_actions:
                    issues.append(
                        f"Execution Envelope stage {index} contains unsupported actions: "
                        + ", ".join(unknown_stage_actions)
                    )
                if isinstance(allowed_actions, list):
                    outside_envelope = sorted(set(actions) - set(allowed_actions))
                    if outside_envelope:
                        issues.append(
                            f"Execution Envelope stage {index} actions are not allowed by the envelope: "
                            + ", ".join(outside_envelope)
                        )
                if disposition == "skip" and actions:
                    issues.append(
                        f"Execution Envelope skipped stage {index} cannot allow actions"
                    )
    return issues


def _envelope_approval_issues(
    envelope: dict[str, Any],
    *,
    require_approved: bool,
) -> list[str]:
    issues: list[str] = []
    if require_approved or envelope.get("status") == "approved":
        if not isinstance(envelope.get("approval_decision_id"), str) or not envelope.get("approval_decision_id", "").strip():
            issues.append("approved Execution Envelope needs approval_decision_id")
        approval_decision_digest = envelope.get("approval_decision_digest")
        if not isinstance(approval_decision_digest, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", approval_decision_digest
        ):
            issues.append("approved Execution Envelope needs approval_decision_digest")
        if not isinstance(envelope.get("approved_at"), str) or not envelope.get("approved_at", "").strip():
            issues.append("approved Execution Envelope needs approved_at")
    return issues


def execution_envelope_issues(
    envelope: Any,
    unit_id: str | None = None,
    *,
    require_approved: bool = False,
    check_expiry: bool = True,
    maximum_agent_level: str | None = None,
) -> list[str]:
    """Validate the permanent structure and current authorization state."""
    if not isinstance(envelope, dict):
        return ["Execution Envelope must be an object"]
    issues = _envelope_identity_issues(
        envelope,
        unit_id,
        require_approved=require_approved,
        check_expiry=check_expiry,
    )
    issues.extend(_envelope_action_issues(envelope, maximum_agent_level))
    issues.extend(_envelope_stage_issues(envelope))
    issues.extend(
        _envelope_approval_issues(envelope, require_approved=require_approved)
    )
    return issues
