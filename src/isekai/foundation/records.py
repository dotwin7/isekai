from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from .types import FOUNDATION_DECISION_FIELDS, FoundationError, FoundationRelease
from .validation import _parse_timestamp


def _foundation_record_digest(record: dict[str, Any], digest_field: str) -> str:
    subject = {key: value for key, value in record.items() if key != digest_field}
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _foundation_decision_digest(decision: dict[str, Any]) -> str:
    return _foundation_record_digest(decision, "decision_digest")


def _foundation_evidence_digest(evidence: dict[str, Any]) -> str:
    return _foundation_record_digest(evidence, "evidence_digest")


def _foundation_decision_attestation_issues(decision: dict[str, Any]) -> list[str]:
    attestation = decision.get("attestation")
    if attestation is None:
        return []
    if not isinstance(attestation, dict):
        return ["Foundation Decision attestation must be an object"]
    issues: list[str] = []
    if attestation.get("type") != "human-decision-attestation":
        issues.append("Foundation Decision attestation has an invalid type")
    if attestation.get("reported_actor") != decision.get("decided_by"):
        issues.append(
            "Foundation Decision attestation reported_actor must match decided_by"
        )
    if attestation.get("identity_verification") != "not-performed-by-core":
        issues.append(
            "Foundation Decision attestation must disclose the Core identity boundary"
        )
    if attestation.get("confirmation_source") != "caller-attested":
        issues.append("Foundation Decision attestation has an invalid confirmation_source")
    return issues


def _foundation_decision_issues(
    foundation: FoundationRelease,
    decision: Any,
    *,
    require_current_approval: bool,
    allow_legacy_approval_digest: bool = False,
) -> list[str]:
    if not isinstance(decision, dict):
        return ["Foundation Decision must be an object"]
    required = set(FOUNDATION_DECISION_FIELDS)
    if allow_legacy_approval_digest:
        # The first v0.1 Decision predates approval_digest. Its migrated digest
        # chain still protects every field that existed at that time.
        required.discard("approval_digest")
    issues: list[str] = []
    missing = sorted(required - decision.keys())
    if missing:
        issues.append(f"Foundation Decision missing fields: {', '.join(missing)}")
    if decision.get("foundation_id") != foundation.manifest["id"]:
        issues.append("Foundation Decision foundation_id does not match release")
    decision_version = decision.get("version")
    if require_current_approval and decision_version != foundation.version:
        issues.append("Foundation Decision version does not match release")
    elif not isinstance(decision_version, str) or not decision_version.strip():
        issues.append("Foundation Decision requires a non-empty version")
    approval_digest = decision.get("approval_digest")
    if require_current_approval:
        if approval_digest != foundation.approval_digest:
            issues.append(
                "Foundation Decision approval_digest does not match release content"
            )
    elif approval_digest is not None and not (
        isinstance(approval_digest, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", approval_digest)
    ):
        issues.append("Foundation Decision approval_digest must be a SHA-256 digest")
    if not isinstance(decision.get("outcome"), str) or decision.get(
        "outcome"
    ) not in {"approved", "rejected"}:
        issues.append("Foundation Decision has an invalid outcome")
    if decision.get("type") != "foundation-release-decision":
        issues.append("Foundation Decision has an invalid type")
    if decision.get("schema_version") != "1.0.0":
        issues.append("Foundation Decision has an unsupported schema_version")
    for field in ("id", "summary", "decided_by"):
        if not isinstance(decision.get(field), str) or not decision.get(
            field, ""
        ).strip():
            issues.append(f"Foundation Decision requires a non-empty {field}")
    try:
        _parse_timestamp(decision.get("decided_at"), "Foundation Decision decided_at")
    except FoundationError as exc:
        issues.append(str(exc))
    decision_digest = decision.get("decision_digest")
    if not isinstance(decision_digest, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", decision_digest
    ):
        issues.append("Foundation Decision decision_digest must be a SHA-256 digest")
    elif decision_digest != _foundation_decision_digest(decision):
        issues.append("Foundation Decision digest does not match its record")
    previous_digest = decision.get("previous_decision_digest")
    if previous_digest is not None and not (
        isinstance(previous_digest, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", previous_digest)
    ):
        issues.append(
            "Foundation Decision previous_decision_digest must be null or a "
            "SHA-256 digest"
        )
    issues.extend(_foundation_decision_attestation_issues(decision))
    return issues


def _foundation_decision_history_issues(
    foundation: FoundationRelease,
    entries: list[Any],
    *,
    require_latest_approval: bool,
) -> list[str]:
    """Validate each Decision and the append-only order of the ledger."""
    issues: list[str] = []
    seen_ids: set[str] = set()
    previous_digest: str | None = None
    previous_decided_at: datetime | None = None
    for index, entry in enumerate(entries):
        issues.extend(
            f"Foundation Decision {index}: {issue}"
            for issue in _foundation_decision_issues(
                foundation,
                entry,
                require_current_approval=(
                    require_latest_approval and index == len(entries) - 1
                ),
                allow_legacy_approval_digest=(
                    index == 0
                    and isinstance(entry, dict)
                    and entry.get("version") == "0.1.0"
                ),
            )
        )
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get("id")
        if isinstance(entry_id, str):
            if entry_id in seen_ids:
                issues.append(f"Foundation Decision {index} has a duplicate id")
            seen_ids.add(entry_id)
        if entry.get("previous_decision_digest") != previous_digest:
            issues.append(
                f"Foundation Decision {index} does not continue the Decision digest chain"
            )
        try:
            decided_at = _parse_timestamp(
                entry.get("decided_at"), f"Foundation Decision {index} decided_at"
            )
        except FoundationError:
            decided_at = None
        if (
            decided_at is not None
            and previous_decided_at is not None
            and decided_at <= previous_decided_at
        ):
            issues.append(
                f"Foundation Decision {index} decided_at must be later than the "
                "previous Decision"
            )
        if decided_at is not None:
            previous_decided_at = decided_at
        digest = entry.get("decision_digest")
        previous_digest = digest if isinstance(digest, str) else None
    return issues
