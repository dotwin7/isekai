from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .common import parse_iso_timestamp as _parse_iso_timestamp
from .proof_receipt import proof_receipt_issues


EVIDENCE_REQUIRED_FIELDS = {
    "id", "type", "schema_version", "unit_id", "stage", "passed", "scope",
    "recorded_by", "recorded_at", "commands", "envelope_id", "envelope_digest",
    "authorization_ledger_digest", "authorization_count",
}
EVIDENCE_COMMAND_REQUIRED_FIELDS = {
    "command", "exit_code", "output_digest", "observed_at", "authorization_id",
}
EVIDENCE_ID_PATTERN = re.compile(r"EVD-[0-9]{20}")


@dataclass(frozen=True)
class EvidenceValidationContext:
    """Typed dependencies used to validate one Evidence record."""

    unit_id: str | None = None
    require_passing: bool = True
    authorization_binding: dict[str, Any] | None = None
    authorization_grants: dict[str, dict[str, Any]] | None = None


def verification_evidence_digest(evidence: dict[str, Any]) -> str:
    subject = {key: value for key, value in evidence.items() if key != "record_digest"}
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _attestation_issues(evidence: dict[str, Any]) -> list[str]:
    attestation = evidence.get("attestation")
    if attestation is None:
        return []
    if not isinstance(attestation, dict):
        return ["verification evidence attestation must be an object"]
    issues: list[str] = []
    if attestation.get("type") != "runtime-execution-attestation":
        issues.append("verification evidence attestation has an invalid type")
    if attestation.get("reported_actor") != evidence.get("recorded_by"):
        issues.append(
            "verification evidence attestation reported_actor must match recorded_by"
        )
    expected_execution_verification = (
        "core-proof-receipt"
        if evidence.get("schema_version") == "1.1.0"
        else "not-performed-by-core"
    )
    if attestation.get("execution_verification") != expected_execution_verification:
        issues.append(
            "verification evidence attestation must disclose the Core execution boundary"
        )
    if attestation.get("identity_verification") != "not-performed-by-core":
        issues.append(
            "verification evidence attestation must disclose the Core identity boundary"
        )
    output_verification = attestation.get("output_digest_verification")
    if not isinstance(output_verification, str) or output_verification not in {
        "caller-supplied", "core-derived", "mixed", "core-receipt-derived",
    }:
        issues.append(
            "verification evidence attestation has an invalid output_digest_verification"
        )
    return issues


def _record_issues(
    evidence: dict[str, Any],
    context: EvidenceValidationContext,
) -> tuple[list[str], datetime | None]:
    issues: list[str] = []
    missing_fields = sorted(EVIDENCE_REQUIRED_FIELDS - evidence.keys())
    if missing_fields:
        issues.append(
            f"verification evidence missing fields: {', '.join(missing_fields)}"
        )
    if context.unit_id is not None and evidence.get("unit_id") != context.unit_id:
        issues.append("verification evidence unit_id does not match Unit")
    if evidence.get("type") != "verification-evidence":
        issues.append("verification evidence has an invalid type")
    schema_version = evidence.get("schema_version")
    if not isinstance(schema_version, str) or schema_version not in ("1.0.0", "1.1.0"):
        issues.append("verification evidence has an unsupported schema_version")
    if schema_version == "1.1.0":
        record_digest = evidence.get("record_digest")
        if not isinstance(record_digest, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", record_digest
        ):
            issues.append("verification evidence record_digest must be a SHA-256 digest")
        elif record_digest != verification_evidence_digest(evidence):
            issues.append("verification evidence record_digest does not match its record")
    evidence_id = evidence.get("id")
    if not isinstance(evidence_id, str) or not EVIDENCE_ID_PATTERN.fullmatch(evidence_id):
        issues.append("verification evidence has an invalid id")
    for field in ("stage", "scope", "recorded_by"):
        if not isinstance(evidence.get(field), str) or not evidence.get(field, "").strip():
            label = "a scope" if field == "scope" else (
                "recorded_by provenance" if field == "recorded_by" else "stage"
            )
            issues.append(f"verification evidence requires {label}")
    if not isinstance(evidence.get("passed"), bool):
        issues.append("verification evidence passed must be boolean")
    issues.extend(_attestation_issues(evidence))
    recorded_at = _parse_iso_timestamp(evidence.get("recorded_at"))
    if recorded_at is None:
        issues.append("verification evidence recorded_at must be an ISO-8601 timestamp")
    for field in ("envelope_id", "envelope_digest", "authorization_ledger_digest"):
        if not isinstance(evidence.get(field), str) or not evidence.get(field, "").strip():
            issues.append(f"verification evidence requires {field}")
    for field in ("envelope_digest", "authorization_ledger_digest"):
        value = evidence.get(field)
        if isinstance(value, str) and not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            issues.append(f"verification evidence {field} must be a SHA-256 digest")
    authorization_count = evidence.get("authorization_count")
    if (
        not isinstance(authorization_count, int)
        or isinstance(authorization_count, bool)
        or authorization_count < 0
    ):
        issues.append(
            "verification evidence authorization_count must be a non-negative integer"
        )
    if context.authorization_binding is not None:
        if any(
            evidence.get(field) != expected
            for field, expected in context.authorization_binding.items()
        ):
            issues.append(
                "verification evidence is stale because the authorization ledger changed"
            )
    return issues, recorded_at


def _latest_test_authorization_ids(
    grants: dict[str, dict[str, Any]] | None,
    command_count: int,
) -> list[str]:
    if grants is None:
        return []
    return [
        grant_id
        for grant_id, grant in sorted(
            grants.items(),
            key=lambda item: item[1].get("iteration", 0),
        )
        if grant.get("action") == "test"
    ][-command_count:]


def _external_authorization_issues(
    *,
    command: dict[str, Any],
    command_index: int,
    authorization_id: Any,
    evidence_stage: Any,
    observed_at: datetime | None,
    grants: dict[str, dict[str, Any]] | None,
    used_ids: set[str],
) -> list[str]:
    external_ids = command.get("external_authorization_ids", [])
    if not isinstance(external_ids, list) or any(
        not isinstance(item, str) or not item.strip() for item in external_ids
    ):
        return [
            f"evidence command {command_index} external_authorization_ids must be a list of strings"
        ]
    issues: list[str] = []
    for external_id in external_ids:
        if external_id in used_ids:
            issues.append(
                f"evidence command {command_index} reuses external authorization_id: "
                f"{external_id}"
            )
            continue
        used_ids.add(external_id)
        if grants is None:
            continue
        external_grant = grants.get(external_id)
        test_grant = grants.get(authorization_id) if isinstance(authorization_id, str) else None
        if external_grant is None or external_grant.get("action") != "external-api":
            issues.append(
                f"evidence command {command_index} is not bound to a current "
                f"external-api authorization: {external_id}"
            )
            continue
        external_authorized_at = _parse_iso_timestamp(
            external_grant.get("authorized_at")
        )
        if (
            observed_at is not None
            and external_authorized_at is not None
            and observed_at < external_authorized_at
        ):
            issues.append(
                f"evidence command {command_index} observed_at precedes external "
                f"authorization: {external_id}"
            )
        if external_grant.get("stage") != evidence_stage:
            issues.append(
                f"evidence command {command_index} external authorization stage "
                "does not match Evidence stage"
            )
        if (
            isinstance(test_grant, dict)
            and isinstance(external_grant.get("iteration"), int)
            and isinstance(test_grant.get("iteration"), int)
            and external_grant["iteration"] >= test_grant["iteration"]
        ):
            issues.append(
                f"evidence command {command_index} external authorization must "
                "precede its test authorization"
            )
    return issues


def _command_issues(
    evidence: dict[str, Any],
    context: EvidenceValidationContext,
    recorded_at: datetime | None,
) -> list[str]:
    commands = evidence.get("commands")
    if not isinstance(commands, list) or not commands:
        return ["verification evidence has no commands"]
    issues: list[str] = []
    used_authorizations: set[str] = set()
    used_external_authorizations: set[str] = set()
    grants = context.authorization_grants
    latest_test_ids = _latest_test_authorization_ids(grants, len(commands))
    for index, command in enumerate(commands):
        if not isinstance(command, dict):
            issues.append(f"evidence command {index} must be an object")
            continue
        missing = sorted(EVIDENCE_COMMAND_REQUIRED_FIELDS - command.keys())
        if missing:
            issues.append(
                f"evidence command {index} missing fields: {', '.join(missing)}"
            )
            continue
        if not isinstance(command.get("command"), str) or not command["command"].strip():
            issues.append(f"evidence command {index} requires command text")
        exit_code = command.get("exit_code")
        if (
            (not isinstance(exit_code, int) or isinstance(exit_code, bool))
            and not (evidence.get("passed") is False and exit_code is None)
        ):
            issues.append(f"evidence command {index} exit_code must be an integer")
        digest = command.get("output_digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            issues.append(
                f"evidence command {index} output_digest must be a SHA-256 hex digest"
            )
        observed_at = _parse_iso_timestamp(command.get("observed_at"))
        if observed_at is None:
            issues.append(
                f"evidence command {index} observed_at must be an ISO-8601 timestamp"
            )
        elif recorded_at is not None and observed_at > recorded_at:
            issues.append(
                f"evidence command {index} observed_at is after Evidence recorded_at"
            )
        authorization_id = command.get("authorization_id")
        if not isinstance(authorization_id, str) or not authorization_id.strip():
            issues.append(f"evidence command {index} requires authorization_id")
        elif authorization_id in used_authorizations:
            issues.append(
                f"evidence command {index} reuses authorization_id: {authorization_id}"
            )
        else:
            used_authorizations.add(authorization_id)
            if grants is not None:
                grant = grants.get(authorization_id)
                if grant is None or grant.get("action") != "test":
                    issues.append(
                        f"evidence command {index} is not bound to a current test authorization"
                    )
                else:
                    issues.extend(
                        f"evidence command {index} {issue}"
                        for issue in proof_receipt_issues(
                            grant, command, passed=evidence.get("passed") is True
                        )
                    )
                    authorized_at = _parse_iso_timestamp(grant.get("authorized_at"))
                    if (
                        observed_at is not None
                        and authorized_at is not None
                        and observed_at < authorized_at
                    ):
                        issues.append(
                            f"evidence command {index} observed_at precedes its authorization"
                        )
                    if latest_test_ids and (
                        index >= len(latest_test_ids)
                        or authorization_id != latest_test_ids[index]
                    ):
                        issues.append(
                            f"evidence command {index} must use the latest authorized test actions"
                        )
                    if grant.get("stage") != evidence.get("stage"):
                        issues.append(
                            f"evidence command {index} authorization stage does not match Evidence stage"
                        )
        issues.extend(
            _external_authorization_issues(
                command=command,
                command_index=index,
                authorization_id=authorization_id,
                evidence_stage=evidence.get("stage"),
                observed_at=observed_at,
                grants=grants,
                used_ids=used_external_authorizations,
            )
        )
        if evidence.get("passed") is True and exit_code != 0:
            issues.append(
                f"passing verification evidence has non-zero command {index} exit_code"
            )
    if grants is not None:
        last_command = commands[-1]
        last_id = last_command.get("authorization_id") if isinstance(last_command, dict) else None
        last_grant = grants.get(last_id) if isinstance(last_id, str) else None
        latest_iteration = max(
            (
                grant.get("iteration", 0)
                for grant in grants.values()
                if isinstance(grant.get("iteration"), int)
            ),
            default=0,
        )
        if isinstance(last_grant, dict) and last_grant.get("iteration") != latest_iteration:
            issues.append("evidence commands must use the latest authorized test actions")
    return issues


def evidence_issues(
    evidence: Any,
    context: EvidenceValidationContext,
) -> list[str]:
    if not isinstance(evidence, dict):
        return ["verification evidence must be an object"]
    issues, recorded_at = _record_issues(evidence, context)
    issues.extend(_command_issues(evidence, context, recorded_at))
    if context.require_passing and evidence.get("passed") is not True:
        issues.append("verification evidence is not passing")
    return issues


__all__ = [
    "EVIDENCE_ID_PATTERN",
    "EvidenceValidationContext",
    "evidence_issues",
    "verification_evidence_digest",
]
