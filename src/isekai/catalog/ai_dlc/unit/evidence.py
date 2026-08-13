from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from isekai.support.errors import (
    AuthorizationError, EvidenceError, IntegrityError, LifecycleError, PreflightError,
)
from .common import (
    unit_json as _unit_json,
    unit_maximum_agent_level as _unit_maximum_agent_level,
    unit_path_without_symlinks as _unit_path_without_symlinks,
    unit_preflight_issues as _unit_preflight_issues,
    write_unit_json as _write_unit_json,
    unit_lock,
)
from .evidence_validation import (
    EVIDENCE_ID_PATTERN,
    EvidenceValidationContext,
    evidence_issues,
    verification_evidence_digest,
)


EVIDENCE_ALLOWED_STATUSES = {
    "construction",
    "validation",
    "awaiting-release-decision",
    "releasing",
    "operating",
}


def validate_evidence(
    evidence: Any,
    unit_id: str | None = None,
    *,
    require_passing: bool = True,
    authorization_binding: dict[str, Any] | None = None,
    authorization_grants: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    return evidence_issues(
        evidence,
        EvidenceValidationContext(
            unit_id=unit_id,
            require_passing=require_passing,
            authorization_binding=authorization_binding,
            authorization_grants=authorization_grants,
        ),
    )


# Compatibility for the legacy workflow facade; production modules use the
# typed validator name above.
_evidence_issues = validate_evidence


def _passing_evidence(unit_dir: Path) -> bool:
    evidence_path = unit_dir / "evidence/verification.json"
    if not evidence_path.is_file():
        return False
    try:
        evidence = _unit_json(unit_dir, "evidence/verification.json")
        unit = _unit_json(unit_dir, "unit.json")
        binding, grants = _current_authorization_context(
            unit_dir, unit, check_expiry=False
        )
    except ValueError:
        return False
    return not validate_evidence(
        evidence,
        str(unit.get("id")),
        authorization_binding=binding,
        authorization_grants=grants,
    )


def _current_authorization_context(
    unit_dir: Path,
    unit: dict[str, Any],
    *,
    check_expiry: bool,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    from .authorization import (
        authorization_ledger_digest as _authorization_ledger_digest,
        authorization_ledger_issues as _authorization_ledger_issues,
    )
    from .decisions import approved_envelope_decision_issues
    from .execution_schema import execution_envelope_issues

    envelope = _unit_json(unit_dir, "execution-envelope.json")
    ledger = _unit_json(unit_dir, "execution-authorizations.json")
    envelope_issues = execution_envelope_issues(
        envelope,
        str(unit.get("id")),
        require_approved=True,
        check_expiry=check_expiry,
        maximum_agent_level=_unit_maximum_agent_level(unit_dir),
    )
    envelope_issues.extend(
        approved_envelope_decision_issues(unit_dir, envelope, unit)
    )
    if envelope_issues:
        raise AuthorizationError(
            "Execution Envelope blocks Evidence: " + "; ".join(envelope_issues)
        )
    ledger_issues = _authorization_ledger_issues(
        ledger, unit, envelope, unit_dir=unit_dir
    )
    if ledger_issues:
        raise AuthorizationError("Action ledger blocks Evidence: " + "; ".join(ledger_issues))
    grants = {
        str(grant.get("id")): grant
        for grant in ledger["grants"]
        if isinstance(grant, dict) and isinstance(grant.get("id"), str)
    }
    binding = {
        "envelope_id": envelope.get("id"),
        "envelope_digest": envelope.get("approval_digest"),
        "authorization_ledger_digest": _authorization_ledger_digest(ledger),
        "authorization_count": len(ledger["grants"]),
    }
    return binding, grants


def _current_authorization_binding(
    unit_dir: Path,
    unit: dict[str, Any],
) -> dict[str, Any]:
    binding, _grants = _current_authorization_context(
        unit_dir, unit, check_expiry=False
    )
    return binding


def _historical_evidence_issues(
    evidence: Any,
    unit_id: str,
    authorization_contexts: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[str]:
    """Validate one immutable Evidence record against its ledger prefix."""
    if not isinstance(evidence, dict):
        return ["historical verification evidence must be an object"]
    from .authorization import authorization_ledger_digest as _authorization_ledger_digest

    authorization_count = evidence.get("authorization_count")
    if (
        not isinstance(authorization_count, int)
        or isinstance(authorization_count, bool)
        or authorization_count < 0
    ):
        return validate_evidence(evidence, unit_id, require_passing=False)
    for envelope, ledger in authorization_contexts:
        if (
            evidence.get("envelope_id") != envelope.get("id")
            or evidence.get("envelope_digest") != envelope.get("approval_digest")
        ):
            continue
        grants = ledger.get("grants")
        if not isinstance(grants, list) or authorization_count > len(grants):
            continue
        prefix = dict(ledger)
        prefix["grants"] = grants[:authorization_count]
        ledger_digest = _authorization_ledger_digest(prefix)
        if evidence.get("authorization_ledger_digest") != ledger_digest:
            continue
        grant_map = {
            str(grant.get("id")): grant
            for grant in prefix["grants"]
            if isinstance(grant, dict) and isinstance(grant.get("id"), str)
        }
        binding = {
            "envelope_id": envelope.get("id"),
            "envelope_digest": envelope.get("approval_digest"),
            "authorization_ledger_digest": ledger_digest,
            "authorization_count": authorization_count,
        }
        return validate_evidence(
            evidence,
            unit_id,
            require_passing=False,
            authorization_binding=binding,
            authorization_grants=grant_map,
        )
    return [
        "historical verification evidence has no matching authorization ledger prefix"
    ] + validate_evidence(evidence, unit_id, require_passing=False)


def build_command_evidence(
    command: str,
    exit_code: int,
    output: str,
    observed_at: str,
) -> dict[str, Any]:
    if not isinstance(command, str) or not command.strip():
        raise EvidenceError("command must be a non-empty string")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise EvidenceError("exit_code must be an integer")
    if not isinstance(output, str):
        raise EvidenceError("output must be a string")
    if not isinstance(observed_at, str) or not observed_at.strip():
        raise EvidenceError("observed_at must be a non-empty string")
    return {
        "command": command.strip(),
        "exit_code": exit_code,
        "output_digest": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "observed_at": observed_at.strip(),
    }


def _evidence_record_relative(evidence_id: str) -> str:
    """Return the immutable record path for one generated Evidence ID."""
    if not EVIDENCE_ID_PATTERN.fullmatch(evidence_id):
        raise EvidenceError(f"verification evidence has an invalid id: {evidence_id}")
    return f"evidence/records/{evidence_id}.json"


def _persist_evidence_record(
    unit_dir: Path,
    evidence: dict[str, Any],
) -> str:
    """Persist Evidence by ID without allowing a prior record to be replaced."""
    relative = _evidence_record_relative(str(evidence.get("id", "")))
    target = _unit_path_without_symlinks(unit_dir, relative)
    expected_digest = verification_evidence_digest(evidence)
    if target.exists() or target.is_symlink():
        existing = _unit_json(unit_dir, relative)
        if verification_evidence_digest(existing) != expected_digest:
            raise EvidenceError(
                "verification Evidence record conflicts with an existing Evidence ID"
            )
        return relative
    try:
        _write_unit_json(unit_dir, relative, evidence, create_parents=True,
                         replace_existing=False)
    except FileExistsError:
        existing = _unit_json(unit_dir, relative)
        if verification_evidence_digest(existing) != expected_digest:
            raise EvidenceError(
                "verification Evidence record conflicts with an existing Evidence ID"
            ) from None
        return relative
    persisted = _unit_json(unit_dir, relative)
    if verification_evidence_digest(persisted) != expected_digest:
        raise EvidenceError("verification Evidence record postflight failed")
    return relative


def _archive_current_evidence(unit_dir: Path) -> None:
    """Backfill a legacy singleton Evidence before it is replaced."""
    try:
        current = _unit_json(unit_dir, "evidence/verification.json")
    except ValueError:
        return
    evidence_id = current.get("id")
    if isinstance(evidence_id, str) and EVIDENCE_ID_PATTERN.fullmatch(evidence_id):
        _persist_evidence_record(unit_dir, current)


def record_evidence(
    path: str | Path,
    *,
    passed: bool,
    commands: list[dict[str, Any]],
    scope: str,
    recorded_by: str,
    notes: str = "",
) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise EvidenceError(f"Unit directory does not exist: {unit_dir}")
    if not isinstance(passed, bool):
        raise EvidenceError("passed must be boolean")
    if not isinstance(commands, list) or not commands:
        raise EvidenceError("commands must be a non-empty list")
    if not isinstance(scope, str) or not scope.strip():
        raise EvidenceError("scope must be a non-empty string")
    if not isinstance(recorded_by, str) or not recorded_by.strip():
        raise EvidenceError("recorded_by must be a non-empty string")
    if not isinstance(notes, str):
        raise EvidenceError("notes must be a string")

    with unit_lock(unit_dir):
        return _record_evidence_locked(
            unit_dir,
            passed=passed,
            commands=commands,
            scope=scope,
            recorded_by=recorded_by,
            notes=notes,
        )


def _record_evidence_locked(
    unit_dir: Path,
    *,
    passed: bool,
    commands: list[dict[str, Any]],
    scope: str,
    recorded_by: str,
    notes: str,
) -> dict[str, Any]:
    unit = _unit_json(unit_dir, "unit.json")
    preflight_issues = _unit_preflight_issues(unit_dir)
    if preflight_issues:
        raise PreflightError("Evidence preflight blocked: " + "; ".join(preflight_issues))
    status = unit.get("status")
    if not isinstance(status, str) or status not in EVIDENCE_ALLOWED_STATUSES:
        raise LifecycleError(
            "Evidence can only be recorded after Construction begins; current status: "
            + str(unit.get("status"))
        )
    authorization_binding, authorization_grants = _current_authorization_context(
        unit_dir, unit, check_expiry=True
    )
    normalized_commands: list[dict[str, Any]] = []
    core_derived_outputs = 0
    receipt_derived_outputs = 0
    for index, command in enumerate(commands):
        if not isinstance(command, dict):
            raise EvidenceError(f"command {index} must be an object")
        item = dict(command)
        authorization_id = item.get("authorization_id")
        grant = (
            authorization_grants.get(authorization_id)
            if isinstance(authorization_id, str)
            else None
        )
        execution = grant.get("execution") if isinstance(grant, dict) else None
        if isinstance(execution, dict) and execution.get("type") == "core-proof":
            if "output" in item:
                raise EvidenceError(
                    f"command {index} output is derived from its Core proof receipt"
                )
            derived = {
                "command": execution.get("evidence_command"),
                "exit_code": execution.get("exit_code"),
                "output_digest": execution.get("evidence_output_digest"),
                "observed_at": execution.get("completed_at"),
            }
            for field, expected in derived.items():
                if field in item and item[field] != expected:
                    raise IntegrityError(
                        f"command {index} {field} does not match its Core proof receipt"
                    )
            item.update(derived)
            receipt_derived_outputs += 1
        elif "output" in item:
            missing_input_fields = sorted(
                {"command", "exit_code", "observed_at", "authorization_id"}
                - item.keys()
            )
            if missing_input_fields:
                raise EvidenceError(
                    f"command {index} missing fields: "
                    + ", ".join(missing_input_fields)
                )
            core_derived_outputs += 1
            output = item.pop("output")
            computed = build_command_evidence(
                cast(str, item["command"]),
                cast(int, item["exit_code"]),
                output,
                cast(str, item["observed_at"]),
            )
            supplied_digest = item.get("output_digest")
            if (
                supplied_digest is not None
                and supplied_digest != computed["output_digest"]
            ):
                raise IntegrityError(f"command {index} output_digest does not match output")
            item.update(computed)
        normalized_commands.append(item)

    now = datetime.now(timezone.utc)
    if receipt_derived_outputs == len(commands):
        output_digest_verification = "core-receipt-derived"
    elif core_derived_outputs == len(commands):
        output_digest_verification = "core-derived"
    elif core_derived_outputs or receipt_derived_outputs:
        output_digest_verification = "mixed"
    else:
        output_digest_verification = "caller-supplied"

    evidence = {
        "id": "EVD-" + now.strftime("%Y%m%d%H%M%S%f"),
        "type": "verification-evidence",
        "schema_version": "1.1.0",
        "unit_id": unit.get("id"),
        "stage": unit.get("phase"),
        "passed": passed,
        "scope": scope.strip(),
        "recorded_by": recorded_by.strip(),
        "recorded_at": now.isoformat(),
        "attestation": {
            "type": "runtime-execution-attestation",
            "reported_actor": recorded_by.strip(),
            "execution_verification": "core-proof-receipt",
            "identity_verification": "not-performed-by-core",
            "output_digest_verification": output_digest_verification,
        },
        "commands": normalized_commands,
        **authorization_binding,
    }
    if notes.strip():
        evidence["notes"] = notes.strip()
    evidence["record_digest"] = verification_evidence_digest(evidence)
    issues = validate_evidence(
        evidence,
        str(unit.get("id")),
        require_passing=False,
        authorization_binding=authorization_binding,
        authorization_grants=authorization_grants,
    )
    if issues:
        raise EvidenceError("; ".join(issues))
    _archive_current_evidence(unit_dir)
    record_relative = _persist_evidence_record(unit_dir, evidence)
    _write_unit_json(unit_dir, "evidence/verification.json", evidence)
    persisted_evidence = _unit_json(unit_dir, "evidence/verification.json")
    if persisted_evidence.get("id") != evidence["id"]:
        raise EvidenceError("Evidence postflight blocked: record was not persisted")
    return {
        "path": str(unit_dir / "evidence/verification.json"),
        "record_path": str(unit_dir / f"evidence/records/{evidence['id']}.json"),
        "evidence_id": evidence["id"],
        "passed": evidence["passed"],
        "command_count": len(evidence.get("commands", [])),
    }


# Typed internal Evidence storage and binding contract.
current_authorization_context = _current_authorization_context
historical_evidence_issues = _historical_evidence_issues
passing_evidence = _passing_evidence
evidence_record_relative = _evidence_record_relative
persist_evidence_record = _persist_evidence_record
