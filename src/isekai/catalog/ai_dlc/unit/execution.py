from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from isekai.support.locking import LockUnavailable
from isekai.support.errors import AuthorizationError, IntegrityError, LifecycleError, PreflightError
from isekai.catalog.ai_dlc.routing import (
    AGENT_ALLOWED_ACTIONS,
    AGENT_LEVEL_ALLOWED_ACTIONS,
    AGENT_PROHIBITED_ACTIONS,
)
from .authorization import (
    authorization_ledger_issues as _authorization_ledger_issues,
    last_authorization_id as _last_authorization_id,
)
from .authorization_request import authorization_request_type_issue, external_grant_metadata, external_request_count, resolve_authorization_request
from .execution_history import (
    execution_authorization_record_relative as _execution_authorization_record_relative,
    persist_execution_authorization_record as _persist_execution_authorization_record,
)
from .execution_schema import (
    EXECUTION_ENVELOPE_DEFAULT_HOURS as EXECUTION_ENVELOPE_DEFAULT_HOURS,
    EXECUTION_ENVELOPE_MAX_HOURS as EXECUTION_ENVELOPE_MAX_HOURS,
    EXECUTION_ENVELOPE_PROPOSABLE_STATUSES,
    EXECUTION_ENVELOPE_REQUIRED_FIELDS as EXECUTION_ENVELOPE_REQUIRED_FIELDS,
    EXECUTION_ENVELOPE_STATUSES as EXECUTION_ENVELOPE_STATUSES,
    EXECUTION_STAGE_DEPTHS as EXECUTION_STAGE_DEPTHS,
    EXECUTION_STAGE_DISPOSITIONS as EXECUTION_STAGE_DISPOSITIONS,
    execution_envelope_approval_digest,
    execution_envelope_issues,
    scope_pattern_issue as _scope_pattern_issue,
    envelope_scope_pattern_matches as _scope_pattern_matches,
)
from .common import (
    restore_snapshots as _restore_snapshots,
    unlink_unit_file as _unlink_unit_file,
    unit_bytes as _unit_bytes,
    unit_json as _unit_json,
    unit_maximum_agent_level as _unit_maximum_agent_level,
    unit_path_without_symlinks as _unit_path_without_symlinks,
    unit_preflight_issues as _unit_preflight_issues,
    write_unit_json as _write_unit_json,
    unit_lock,
)
from .checkpointing import progress_authorization_block, progress_authorization_obligation
from .decisions import approved_envelope_decision_issues
from .decision_schema import (
    decision_ledger_issues,
    decision_record_issues,
    latest_decision,
)
from .external_access import EXTERNAL_API_ACTION, external_access_policy_issues




def propose_execution_envelope(
    path: str | Path,
    *,
    scope: list[str],
    stages: list[dict[str, Any]],
    allowed_actions: list[str],
    forbidden_actions: list[str],
    max_iterations: int,
    proposed_by: str,
    external_access: list[dict[str, Any]] | None = None,
    expires_in_hours: int = EXECUTION_ENVELOPE_DEFAULT_HOURS,
) -> dict[str, Any]:
    """Propose an Execution Envelope, replacing any Envelope the Unit already has.

    Re-proposing during active work is how an expired Envelope or an exhausted
    iteration budget is renewed. The replacement starts as ``proposed``, so the
    Unit holds no authorization until a new approved inception Decision binds it.
    """
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise AuthorizationError(f"Unit directory does not exist: {unit_dir}")
    normalized_external_access = [] if external_access is None else external_access
    with unit_lock(unit_dir):
        return _propose_execution_envelope_locked(
            unit_dir,
            scope=scope,
            stages=stages,
            allowed_actions=allowed_actions,
            forbidden_actions=forbidden_actions,
            max_iterations=max_iterations,
            proposed_by=proposed_by,
            external_access=normalized_external_access,
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
    external_access: list[dict[str, Any]],
    expires_in_hours: int,
) -> dict[str, Any]:
    preflight_issues = _unit_preflight_issues(unit_dir)
    if preflight_issues:
        raise PreflightError(
            "Execution Envelope proposal blocked: " + "; ".join(preflight_issues)
        )
    unit = _unit_json(unit_dir, "unit.json")
    if unit.get("status") not in EXECUTION_ENVELOPE_PROPOSABLE_STATUSES:
        raise LifecycleError(
            "Execution Envelope cannot be proposed in the current Unit status: "
            + str(unit.get("status"))
        )
    if not isinstance(proposed_by, str) or not proposed_by.strip():
        raise AuthorizationError("proposed_by must be a non-empty string")
    if (
        not isinstance(expires_in_hours, int)
        or isinstance(expires_in_hours, bool)
        or not 0 < expires_in_hours <= EXECUTION_ENVELOPE_MAX_HOURS
    ):
        raise AuthorizationError(
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
        "external_access": external_access,
        "max_iterations": max_iterations,
        "proposed_by": proposed_by.strip(),
        "proposed_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=expires_in_hours)).isoformat(),
    }
    envelope["approval_digest"] = execution_envelope_approval_digest(envelope)
    issues = execution_envelope_issues(
        envelope,
        str(unit.get("id")),
        maximum_agent_level=_unit_maximum_agent_level(unit_dir),
    )
    if issues:
        raise AuthorizationError("Execution Envelope rejected: " + "; ".join(issues))
    ledger = {
        "type": "execution-authorization-ledger",
        "schema_version": "1.0.0",
        "unit_id": unit.get("id"),
        "envelope_id": envelope["id"],
        "approval_digest": envelope["approval_digest"],
        "grants": [],
    }
    envelope_path = unit_dir / "execution-envelope.json"
    ledger_path = unit_dir / "execution-authorizations.json"
    previous_envelope = _unit_bytes(unit_dir, "execution-envelope.json")
    previous_ledger = _unit_bytes(unit_dir, "execution-authorizations.json")
    previous_envelope_record = _unit_json(unit_dir, "execution-envelope.json")
    previous_ledger_record = _unit_json(unit_dir, "execution-authorizations.json")
    previous_grants = previous_ledger_record.get("grants")
    archive_relative: str | None = None
    archive_target: Path | None = None
    archive_preexisting = False
    if isinstance(previous_grants, list) and previous_grants:
        archive_relative = _execution_authorization_record_relative(
            str(previous_envelope_record.get("id", ""))
        )
        archive_target = _unit_path_without_symlinks(unit_dir, archive_relative)
        archive_preexisting = archive_target.exists() or archive_target.is_symlink()
    try:
        _write_unit_json(unit_dir, "execution-envelope.json", envelope)
        _write_unit_json(unit_dir, "execution-authorizations.json", ledger)
        persisted = _unit_json(unit_dir, "execution-envelope.json")
        persisted_ledger = _unit_json(unit_dir, "execution-authorizations.json")
        if (
            persisted.get("id") != envelope["id"]
            or persisted_ledger.get("envelope_id") != envelope["id"]
            or persisted_ledger.get("approval_digest") != envelope["approval_digest"]
        ):
            raise IntegrityError(
                "Execution Envelope postflight blocked: records were not persisted"
            )
        if archive_target is not None:
            _persist_execution_authorization_record(
                unit_dir,
                unit,
                previous_envelope_record,
                previous_ledger_record,
            )
    except Exception as exc:
        archive_cleanup_error: Exception | None = None
        if archive_target is not None and not archive_preexisting:
            try:
                assert archive_relative is not None
                _unlink_unit_file(unit_dir, archive_relative, missing_ok=True)
            except Exception as cleanup_exc:
                archive_cleanup_error = cleanup_exc
        _restore_snapshots(
            [(envelope_path, previous_envelope), (ledger_path, previous_ledger)],
            "Execution Envelope transaction",
            exc,
            root=unit_dir,
        )
        if archive_cleanup_error is not None:
            raise IntegrityError(
                "Execution Envelope transaction failed and authorization archive "
                f"rollback failed: {archive_cleanup_error}"
            ) from exc
        raise
    return {
        "path": str(unit_dir / "execution-envelope.json"),
        "envelope_id": envelope["id"],
        "status": envelope["status"],
        "approval_digest": envelope["approval_digest"],
        "expires_at": envelope["expires_at"],
        "max_iterations": envelope["max_iterations"],
    }


def _approve_execution_envelope(unit_dir: Path, decision: dict[str, Any]) -> None:
    envelope = _unit_json(unit_dir, "execution-envelope.json")
    unit = _unit_json(unit_dir, "unit.json")
    issues = execution_envelope_issues(
        envelope,
        str(unit.get("id")),
        maximum_agent_level=_unit_maximum_agent_level(unit_dir),
    )
    if issues:
        raise AuthorizationError("Execution Envelope approval blocked: " + "; ".join(issues))
    decision_issues = decision_record_issues(
        decision,
        unit_id=str(unit.get("id")),
        scope=str(unit.get("scope")),
    )
    if decision_issues:
        raise IntegrityError(
            "Execution Envelope approval blocked: " + "; ".join(decision_issues)
        )
    references = decision.get("references", [])
    if "execution-envelope.json" not in references:
        raise IntegrityError(
            "Inception Decision must reference execution-envelope.json"
        )
    approval_subject = decision.get("approval_subject")
    if not isinstance(approval_subject, dict):
        raise IntegrityError("Inception Decision has no bound Execution Envelope subject")
    if approval_subject.get("type") != "execution-envelope":
        raise IntegrityError("Inception Decision approval subject has an invalid type")
    if approval_subject.get("id") != envelope.get("id"):
        raise IntegrityError("Execution Envelope was replaced after the Inception Decision")
    if approval_subject.get("digest") != envelope.get("approval_digest"):
        raise IntegrityError("Execution Envelope changed after the Inception Decision")
    envelope["status"] = "approved"
    envelope["approval_decision_id"] = decision["id"]
    envelope["approval_decision_digest"] = decision["decision_digest"]
    envelope["approved_at"] = datetime.now(timezone.utc).isoformat()
    _write_unit_json(unit_dir, "execution-envelope.json", envelope)
    persisted = _unit_json(unit_dir, "execution-envelope.json")
    if persisted.get("status") != "approved":
        raise IntegrityError("Execution Envelope approval postflight blocked")


def approve_execution_envelope(path: str | Path) -> dict[str, Any]:
    """Bind the Unit's current Envelope to its latest approved inception Decision.

    The initial approval happens as part of the transition into Construction.
    This is the renewal path: after a replacement Envelope is proposed and a new
    inception Decision approves it, this activates it without another transition.
    """
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise AuthorizationError(f"Unit directory does not exist: {unit_dir}")
    with unit_lock(unit_dir):
        preflight_issues = _unit_preflight_issues(unit_dir)
        if preflight_issues:
            raise PreflightError(
                "Execution Envelope approval blocked: " + "; ".join(preflight_issues)
            )
        decisions = _unit_json(unit_dir, "decisions.json")
        unit = _unit_json(unit_dir, "unit.json")
        decision_issues = decision_ledger_issues(
            decisions,
            unit_id=str(unit.get("id")),
            scope=str(unit.get("scope")),
        )
        if decision_issues:
            raise IntegrityError(
                "Execution Envelope approval blocked: "
                + "; ".join(decision_issues)
            )
        decision = latest_decision(decisions, "inception")
        if decision is None or decision.get("outcome") != "approved":
            raise LifecycleError("Execution Envelope needs an approved inception Decision")
        _approve_execution_envelope(unit_dir, decision)
        envelope = _unit_json(unit_dir, "execution-envelope.json")
    return {
        "path": str(unit_dir / "execution-envelope.json"),
        "envelope": envelope,
        "approval_decision_id": decision["id"],
    }


def _issue_action_grant(
    path: str | Path,
    *,
    action: str,
    target: str | None = None,
    stage: str | None = None,
    method: str | None = None,
    credential_ref: str | None = None,
) -> dict[str, Any]:
    """Issue a standalone grant for internal validation and compatibility paths."""
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        return {"allowed": False, "reason": f"Unit directory does not exist: {unit_dir}"}
    if not isinstance(action, str):
        return {"allowed": False, "reason": "Action must be a string"}
    if stage is not None and not isinstance(stage, str):
        return {"allowed": False, "reason": "stage must be a string"}
    request_issue = authorization_request_type_issue(target, method, credential_ref)
    if request_issue is not None:
        return {"allowed": False, "reason": request_issue}
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
            return _authorize_action_locked(
                unit_dir,
                action=action,
                target=target,
                stage=stage,
                method=method,
                credential_ref=credential_ref,
            )
    except LockUnavailable as exc:
        return {"allowed": False, "reason": str(exc)}


def _authorize_action_locked(
    unit_dir: Path,
    *,
    action: str,
    target: str | None,
    stage: str | None,
    method: str | None,
    credential_ref: str | None,
) -> dict[str, Any]:
    try:
        unit = _unit_json(unit_dir, "unit.json")
        envelope = _unit_json(unit_dir, "execution-envelope.json")
        ledger = _unit_json(unit_dir, "execution-authorizations.json")
    except IntegrityError as exc:
        return {"allowed": False, "reason": str(exc)}
    preflight = _unit_preflight_issues(unit_dir)
    if preflight:
        return {
            "allowed": False,
            "reason": "Action preflight blocked: " + "; ".join(preflight),
        }
    envelope_issues = execution_envelope_issues(
        envelope,
        str(unit.get("id")),
        require_approved=True,
        maximum_agent_level=_unit_maximum_agent_level(unit_dir),
    )
    if envelope_issues:
        return {
            "allowed": False,
            "reason": "Action blocked: " + "; ".join(envelope_issues),
        }
    decision_issues = approved_envelope_decision_issues(
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
    normalized_target, external_policy, external_request, target_issue = (
        resolve_authorization_request(
            unit_dir,
            action=action,
            target=target,
            method=method,
            credential_ref=credential_ref,
            envelope=envelope,
        )
    )
    if target_issue is not None:
        return {"allowed": False, "reason": target_issue}
    assert normalized_target is not None
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
    if external_policy is not None:
        policy_request_count = external_request_count(grants, external_policy)
        if policy_request_count >= external_policy["max_requests"]:
            return {
                "allowed": False,
                "reason": (
                    "External API request budget is exhausted for policy: "
                    f"{external_policy['id']}"
                ),
            }
    checkpoint_block = progress_authorization_block(unit_dir, action, ledger)
    if checkpoint_block is not None:
        return {"allowed": False, "reason": checkpoint_block}
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
    if external_policy is not None and external_request is not None:
        grant.update(external_grant_metadata(external_policy, external_request))
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
    _write_unit_json(unit_dir, "execution-authorizations.json", candidate_ledger)
    persisted = _unit_json(unit_dir, "execution-authorizations.json")
    persisted_issues = _authorization_ledger_issues(
        persisted, unit, envelope, unit_dir=unit_dir
    )
    if persisted_issues or _last_authorization_id(persisted) != grant["id"]:
        _write_unit_json(unit_dir, "execution-authorizations.json", ledger)
        return {
            "allowed": False,
            "reason": "Authorization receipt postflight failed",
        }
    result = {
        "allowed": True,
        "unit_id": unit.get("id"),
        "stage": current_stage,
        "action": action,
        "target": normalized_target,
        "iteration": iteration,
        "remaining_iterations": envelope["max_iterations"] - iteration,
        "authorization_id": grant["id"],
    }
    result.update(progress_authorization_obligation(action))
    if external_policy is not None and external_request is not None:
        result.update(
            {
                "external_access_id": external_policy["id"],
                "environment": external_policy["environment"],
                "method": external_request["method"],
                "credential_ref": external_request["credential_ref"],
                "remaining_requests": external_policy["max_requests"]
                - policy_request_count
                - 1,
            }
        )
    return result


# Typed internal execution transaction contract.
approve_execution_envelope_locked = _approve_execution_envelope
authorize_action_locked = _authorize_action_locked
issue_action_grant = _issue_action_grant
