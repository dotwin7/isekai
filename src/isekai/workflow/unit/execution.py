from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ...support.locking import LockUnavailable
from ...support.scope import scope_pattern_matches
from ..routing import AGENT_ALLOWED_ACTIONS, AGENT_PROHIBITED_ACTIONS
from .authorization import (
    _authorization_ledger_issues,
    _authorization_target_protection_issue,
    _normalize_authorization_target,
)
from .common import (
    _unit_json,
    _unit_preflight_issues,
    _write_json,
    unit_lock,
)
from .decisions import (
    _approved_envelope_decision_issues,
    _decision_record_issues,
    _latest_decision,
)


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
# An Envelope bounds how long an approval keeps authorizing actions. Units are
# meant to span sessions, so the default is a working week rather than a day,
# and an expired Envelope is renewed through a fresh human Decision.
EXECUTION_ENVELOPE_DEFAULT_HOURS = 168
EXECUTION_ENVELOPE_MAX_HOURS = 720
# Statuses in which an Envelope may be proposed or re-proposed. Re-proposing
# revokes the active approval until a new Inception Decision approves it.
EXECUTION_ENVELOPE_PROPOSABLE_STATUSES = {
    "proposed",
    "inception",
    "awaiting-inception-decision",
    "construction",
    "awaiting-release-decision",
    "releasing",
    "operating",
}
EXECUTION_ENVELOPE_APPROVAL_FIELDS = {
    "id",
    "type",
    "schema_version",
    "unit_id",
    "scope",
    "stages",
    "allowed_actions",
    "forbidden_actions",
    "max_iterations",
    "proposed_by",
    "proposed_at",
    "expires_at",
}


def _execution_envelope_approval_digest(envelope: dict[str, Any]) -> str:
    subject = {
        field: envelope.get(field)
        for field in sorted(EXECUTION_ENVELOPE_APPROVAL_FIELDS)
    }
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


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
    """Match a scope pattern against a project-relative target path.

    An approved scope is an authorization boundary, so wildcards must not be
    wider than they read: ``*`` and ``?`` stay within one path segment, and only
    a whole ``**`` segment spans directories. Bare ``fnmatch`` would let
    ``src/*.py`` reach arbitrarily deep paths.
    """
    return scope_pattern_matches(pattern, target)


def _execution_envelope_issues(
    envelope: Any,
    unit_id: str | None = None,
    *,
    require_approved: bool = False,
    check_expiry: bool = True,
) -> list[str]:
    """Report structural problems with an Envelope.

    ``check_expiry`` separates the two questions an Envelope answers. Structure
    and binding are permanent properties, so ``verify_unit`` checks them for the
    whole life of a Unit. Expiry only decides whether the approval still
    authorizes new actions, so it is checked when granting or binding an
    approval - never when auditing a Unit that has already moved on.
    """
    if not isinstance(envelope, dict):
        return ["Execution Envelope must be an object"]
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
    if envelope.get("status") not in EXECUTION_ENVELOPE_STATUSES:
        issues.append("Execution Envelope has an invalid status")
    approval_digest = envelope.get("approval_digest")
    if not isinstance(approval_digest, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", approval_digest
    ):
        issues.append("Execution Envelope approval_digest must be a SHA-256 digest")
    elif approval_digest != _execution_envelope_approval_digest(envelope):
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
        if isinstance(forbidden_actions, list) and all(
            isinstance(item, str) for item in forbidden_actions
        ):
            overlap = sorted(set(allowed_actions) & set(forbidden_actions))
            if overlap:
                issues.append(
                    "Execution Envelope actions cannot be both allowed and forbidden: "
                    + ", ".join(overlap)
                )
    max_iterations = envelope.get("max_iterations")
    if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations <= 0:
        issues.append("Execution Envelope max_iterations must be a positive integer")
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
            if not isinstance(stage.get("depth"), str) or not stage["depth"].strip():
                issues.append(f"Execution Envelope stage {index} needs depth")
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


def propose_execution_envelope(
    path: str | Path,
    *,
    scope: list[str],
    stages: list[dict[str, Any]],
    allowed_actions: list[str],
    forbidden_actions: list[str],
    max_iterations: int,
    proposed_by: str,
    expires_in_hours: int = EXECUTION_ENVELOPE_DEFAULT_HOURS,
) -> dict[str, Any]:
    """Propose an Execution Envelope, replacing any Envelope the Unit already has.

    Re-proposing during active work is how an expired Envelope or an exhausted
    iteration budget is renewed. The replacement starts as ``proposed``, so the
    Unit holds no authorization until a new approved inception Decision binds it.
    """
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise ValueError(f"Unit directory does not exist: {unit_dir}")
    with unit_lock(unit_dir):
        return _propose_execution_envelope_locked(
            unit_dir,
            scope=scope,
            stages=stages,
            allowed_actions=allowed_actions,
            forbidden_actions=forbidden_actions,
            max_iterations=max_iterations,
            proposed_by=proposed_by,
            expires_in_hours=expires_in_hours,
        )


def _propose_execution_envelope_locked(
    unit_dir: Path,
    *,
    scope: list[str],
    stages: list[dict[str, Any]],
    allowed_actions: list[str],
    forbidden_actions: list[str],
    max_iterations: int,
    proposed_by: str,
    expires_in_hours: int,
) -> dict[str, Any]:
    unit = _unit_json(unit_dir, "unit.json")
    if unit.get("status") not in EXECUTION_ENVELOPE_PROPOSABLE_STATUSES:
        raise ValueError(
            "Execution Envelope cannot be proposed in the current Unit status: "
            + str(unit.get("status"))
        )
    if not isinstance(proposed_by, str) or not proposed_by.strip():
        raise ValueError("proposed_by must be a non-empty string")
    if (
        not isinstance(expires_in_hours, int)
        or isinstance(expires_in_hours, bool)
        or not 0 < expires_in_hours <= EXECUTION_ENVELOPE_MAX_HOURS
    ):
        raise ValueError(
            "expires_in_hours must be a positive integer of at most "
            f"{EXECUTION_ENVELOPE_MAX_HOURS}"
        )
    now = datetime.now(timezone.utc)
    envelope = {
        "id": f"ENV-{unit.get('id')}-{now.strftime('%Y%m%d%H%M%S%f')}",
        "type": "execution-envelope",
        "schema_version": "1.0.0",
        "unit_id": unit.get("id"),
        "status": "proposed",
        "scope": scope,
        "stages": stages,
        "allowed_actions": allowed_actions,
        "forbidden_actions": forbidden_actions,
        "max_iterations": max_iterations,
        "proposed_by": proposed_by.strip(),
        "proposed_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=expires_in_hours)).isoformat(),
    }
    envelope["approval_digest"] = _execution_envelope_approval_digest(envelope)
    issues = _execution_envelope_issues(envelope, str(unit.get("id")))
    if issues:
        raise ValueError("Execution Envelope rejected: " + "; ".join(issues))
    _write_json(unit_dir / "execution-envelope.json", envelope)
    _write_json(
        unit_dir / "execution-authorizations.json",
        {
            "type": "execution-authorization-ledger",
            "schema_version": "1.0.0",
            "unit_id": unit.get("id"),
            "envelope_id": envelope["id"],
            "approval_digest": envelope["approval_digest"],
            "grants": [],
        },
    )
    persisted = _unit_json(unit_dir, "execution-envelope.json")
    if persisted.get("id") != envelope["id"]:
        raise ValueError("Execution Envelope postflight blocked: record was not persisted")
    return {"path": str(unit_dir / "execution-envelope.json"), "envelope": envelope}


def _approve_execution_envelope(unit_dir: Path, decision: dict[str, Any]) -> None:
    envelope = _unit_json(unit_dir, "execution-envelope.json")
    unit = _unit_json(unit_dir, "unit.json")
    issues = _execution_envelope_issues(envelope, str(unit.get("id")))
    if issues:
        raise ValueError("Execution Envelope approval blocked: " + "; ".join(issues))
    decision_issues = _decision_record_issues(
        decision,
        unit_id=str(unit.get("id")),
        scope=str(unit.get("scope")),
    )
    if decision_issues:
        raise ValueError(
            "Execution Envelope approval blocked: " + "; ".join(decision_issues)
        )
    references = decision.get("references", [])
    if "execution-envelope.json" not in references:
        raise ValueError(
            "Inception Decision must reference execution-envelope.json"
        )
    approval_subject = decision.get("approval_subject")
    if not isinstance(approval_subject, dict):
        raise ValueError("Inception Decision has no bound Execution Envelope subject")
    if approval_subject.get("type") != "execution-envelope":
        raise ValueError("Inception Decision approval subject has an invalid type")
    if approval_subject.get("id") != envelope.get("id"):
        raise ValueError("Execution Envelope was replaced after the Inception Decision")
    if approval_subject.get("digest") != envelope.get("approval_digest"):
        raise ValueError("Execution Envelope changed after the Inception Decision")
    envelope["status"] = "approved"
    envelope["approval_decision_id"] = decision["id"]
    envelope["approval_decision_digest"] = decision["decision_digest"]
    envelope["approved_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(unit_dir / "execution-envelope.json", envelope)
    persisted = _unit_json(unit_dir, "execution-envelope.json")
    if persisted.get("status") != "approved":
        raise ValueError("Execution Envelope approval postflight blocked")


def approve_execution_envelope(path: str | Path) -> dict[str, Any]:
    """Bind the Unit's current Envelope to its latest approved inception Decision.

    The initial approval happens as part of the transition into Construction.
    This is the renewal path: after a replacement Envelope is proposed and a new
    inception Decision approves it, this activates it without another transition.
    """
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise ValueError(f"Unit directory does not exist: {unit_dir}")
    with unit_lock(unit_dir):
        preflight_issues = _unit_preflight_issues(unit_dir)
        if preflight_issues:
            raise ValueError(
                "Execution Envelope approval blocked: " + "; ".join(preflight_issues)
            )
        decisions = _unit_json(unit_dir, "decisions.json")
        unit = _unit_json(unit_dir, "unit.json")
        if decisions.get("unit_id") != unit.get("id"):
            raise ValueError("decisions.json unit_id does not match Unit")
        decision = _latest_decision(decisions, "inception")
        if decision is None or decision.get("outcome") != "approved":
            raise ValueError("Execution Envelope needs an approved inception Decision")
        _approve_execution_envelope(unit_dir, decision)
        envelope = _unit_json(unit_dir, "execution-envelope.json")
    return {
        "path": str(unit_dir / "execution-envelope.json"),
        "envelope": envelope,
        "approval_decision_id": decision["id"],
    }


def authorize_action(
    path: str | Path,
    *,
    action: str,
    target: str | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        return {"allowed": False, "reason": f"Unit directory does not exist: {unit_dir}"}
    if action in AGENT_PROHIBITED_ACTIONS:
        return {
            "allowed": False,
            "reason": f"Action is forbidden by the local Agent contract: {action}",
        }
    if action not in AGENT_ALLOWED_ACTIONS:
        return {
            "allowed": False,
            "reason": f"Action is not supported by the local Agent contract: {action}",
        }
    try:
        with unit_lock(unit_dir):
            try:
                unit = _unit_json(unit_dir, "unit.json")
                envelope = _unit_json(unit_dir, "execution-envelope.json")
                ledger = _unit_json(unit_dir, "execution-authorizations.json")
            except ValueError as exc:
                return {"allowed": False, "reason": str(exc)}
            preflight = _unit_preflight_issues(unit_dir)
            if preflight:
                return {
                    "allowed": False,
                    "reason": "Action preflight blocked: " + "; ".join(preflight),
                }
            envelope_issues = _execution_envelope_issues(
                envelope,
                str(unit.get("id")),
                require_approved=True,
            )
            if envelope_issues:
                return {
                    "allowed": False,
                    "reason": "Action blocked: " + "; ".join(envelope_issues),
                }
            decision_issues = _approved_envelope_decision_issues(
                unit_dir, envelope, unit
            )
            if decision_issues:
                return {
                    "allowed": False,
                    "reason": "Action blocked: " + "; ".join(decision_issues),
                }
            if action in envelope["forbidden_actions"]:
                return {
                    "allowed": False,
                    "reason": f"Action is forbidden by the Execution Envelope: {action}",
                }
            if action not in envelope["allowed_actions"]:
                return {
                    "allowed": False,
                    "reason": f"Action is not allowed by the Execution Envelope: {action}",
                }
            current_stage = unit.get("phase")
            if stage is not None and stage != current_stage:
                return {
                    "allowed": False,
                    "reason": (
                        f"Requested stage {stage} does not match the Unit phase: "
                        f"{current_stage}"
                    ),
                }
            stage_matches = [
                item
                for item in envelope["stages"]
                if item.get("name") == current_stage
            ]
            if not stage_matches:
                return {
                    "allowed": False,
                    "reason": f"No approved Envelope stage for: {current_stage}",
                }
            if action not in stage_matches[0].get("allowed_actions", []):
                return {
                    "allowed": False,
                    "reason": (
                        f"Action is not allowed in stage {current_stage}: {action}"
                    ),
                }
            normalized_target, target_issue = _normalize_authorization_target(
                unit_dir, str(target) if target is not None else ""
            )
            if target_issue is not None:
                return {"allowed": False, "reason": target_issue}
            assert normalized_target is not None
            protection_issue = _authorization_target_protection_issue(
                unit_dir, action, normalized_target
            )
            if protection_issue is not None:
                return {"allowed": False, "reason": protection_issue}
            if not any(
                _scope_pattern_matches(pattern, normalized_target)
                for pattern in envelope["scope"]
            ):
                return {
                    "allowed": False,
                    "reason": (
                        "Target is outside the approved Envelope scope: "
                        f"{normalized_target}"
                    ),
                }
            ledger_issues = _authorization_ledger_issues(
                ledger, unit, envelope, unit_dir=unit_dir
            )
            if ledger_issues:
                return {
                    "allowed": False,
                    "reason": "Action blocked: " + "; ".join(ledger_issues),
                }
            grants = ledger["grants"]
            if len(grants) >= envelope["max_iterations"]:
                return {
                    "allowed": False,
                    "reason": "Execution Envelope max_iterations budget is exhausted",
                }
            now = datetime.now(timezone.utc)
            iteration = len(grants) + 1
            grant = {
                "id": "AUTH-" + now.strftime("%Y%m%d%H%M%S%f"),
                "action": action,
                "target": normalized_target,
                "stage": current_stage,
                "iteration": iteration,
                "decision_id": envelope.get("approval_decision_id"),
                "envelope_digest": envelope.get("approval_digest"),
                "authorized_at": now.isoformat(),
            }
            candidate_ledger = {
                **ledger,
                "grants": [*grants, grant],
            }
            candidate_issues = _authorization_ledger_issues(
                candidate_ledger, unit, envelope, unit_dir=unit_dir
            )
            if candidate_issues:
                return {
                    "allowed": False,
                    "reason": "Authorization receipt rejected: "
                    + "; ".join(candidate_issues),
                }
            _write_json(
                unit_dir / "execution-authorizations.json", candidate_ledger
            )
            persisted = _unit_json(unit_dir, "execution-authorizations.json")
            persisted_issues = _authorization_ledger_issues(
                persisted, unit, envelope, unit_dir=unit_dir
            )
            if persisted_issues or persisted.get("grants", [])[-1].get("id") != grant["id"]:
                # A denied authorization must never consume budget or leave an
                # invalid grant that blocks every later action.
                _write_json(unit_dir / "execution-authorizations.json", ledger)
                return {
                    "allowed": False,
                    "reason": "Authorization receipt postflight failed",
                }
            return {
                "allowed": True,
                "reason": "Action is within the approved Execution Envelope",
                "unit_id": unit.get("id"),
                "stage": current_stage,
                "action": action,
                "target": normalized_target,
                "iteration": iteration,
                "remaining_iterations": envelope["max_iterations"] - iteration,
                "authorization_id": grant["id"],
            }
    except LockUnavailable as exc:
        return {"allowed": False, "reason": str(exc)}
