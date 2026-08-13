from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from .artifacts import artifact_snapshot_issues
from .common import (
    is_iso_timestamp as _is_iso_timestamp,
    parse_iso_timestamp as _parse_iso_timestamp,
)


LIFECYCLE_STATUSES = (
    "proposed",
    "inception",
    "awaiting-inception-decision",
    "construction",
    "validation",
    "awaiting-release-decision",
    "releasing",
    "operating",
    "learned",
    "abandoned",
)
TERMINAL_STATUSES = frozenset({"learned", "abandoned"})

ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "proposed": ("inception", "abandoned"),
    "inception": ("awaiting-inception-decision", "abandoned"),
    "awaiting-inception-decision": ("construction", "abandoned"),
    "construction": ("validation", "abandoned"),
    "validation": ("awaiting-release-decision", "abandoned"),
    "awaiting-release-decision": ("releasing", "abandoned"),
    "releasing": ("operating", "abandoned"),
    "operating": ("learned", "abandoned"),
    "learned": (),
    "abandoned": (),
}

DECISION_GATES = (
    "inception",
    "architecture",
    "release",
    "operation",
    "knowledge",
    "amendment",
    "abandonment",
)
DECISION_OUTCOMES = ("approved", "rejected")
DECISION_ALLOWED_STATUSES = {
    "inception": {
        "awaiting-inception-decision",
        "construction",
        "validation",
        "awaiting-release-decision",
        "releasing",
        "operating",
    },
    "architecture": {"construction"},
    "release": {"awaiting-release-decision", "releasing"},
    "operation": {"operating"},
    "knowledge": {"operating", "learned"},
    "abandonment": {
        status for status in LIFECYCLE_STATUSES if status not in TERMINAL_STATUSES
    },
}
REQUIRED_DECISIONS_FOR_TRANSITIONS = {
    "construction": "inception",
    "validation": "architecture",
    "awaiting-release-decision": "architecture",
    "releasing": "release",
    "operating": "release",
    "learned": "operation",
    "abandoned": "abandonment",
}
STATUS_PHASE = {
    "proposed": "inception",
    "inception": "inception",
    "awaiting-inception-decision": "inception",
    "construction": "construction",
    "validation": "validation",
    "awaiting-release-decision": "validation",
    "releasing": "release",
    "operating": "operations",
    "learned": "operations",
    "abandoned": "closed",
}
DECISION_PACKET_VERSION = "1.0.0"
DECISION_REQUIRED_FIELDS = {
    "id",
    "type",
    "schema_version",
    "unit_id",
    "gate",
    "outcome",
    "summary",
    "scope",
    "decision_packet_version",
    "rationale",
    "alternatives",
    "tradeoffs",
    "risks",
    "references",
    "decided_by",
    "decided_at",
    "previous_decision_digest",
    "decision_digest",
}

DECISION_PACKET_FIELDS = {
    "decision_packet_version",
    "rationale",
    "alternatives",
    "tradeoffs",
    "risks",
    "references",
}


def decision_record_digest(decision: dict[str, Any]) -> str:
    """Bind every auditable Decision field to one canonical digest."""
    subject = {
        key: value for key, value in decision.items() if key != "decision_digest"
    }
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def decision_packet_issues(decision: Any) -> list[str]:
    if not isinstance(decision, dict):
        return ["Decision Packet must be an object"]
    issues: list[str] = []
    missing = sorted(DECISION_PACKET_FIELDS - decision.keys())
    if missing:
        issues.append(f"Decision Packet missing fields: {', '.join(missing)}")
    if decision.get("decision_packet_version") != DECISION_PACKET_VERSION:
        issues.append("Decision Packet has an unsupported version")
    rationale = decision.get("rationale")
    if not isinstance(rationale, list) or not rationale or any(
        not isinstance(item, str) or not item.strip() for item in rationale
    ):
        issues.append("Decision Packet rationale must be a non-empty list of strings")
    for field in ("tradeoffs", "risks", "references"):
        value = decision.get(field)
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            issues.append(f"Decision Packet {field} must be a list of strings")
    alternatives = decision.get("alternatives")
    if not isinstance(alternatives, list):
        issues.append("Decision Packet alternatives must be a list")
    else:
        for index, alternative in enumerate(alternatives):
            if not isinstance(alternative, dict):
                issues.append(f"Decision Packet alternative {index} must be an object")
                continue
            if not isinstance(alternative.get("option"), str) or not alternative["option"].strip():
                issues.append(f"Decision Packet alternative {index} needs option")
            if not isinstance(alternative.get("reason"), str) or not alternative["reason"].strip():
                issues.append(f"Decision Packet alternative {index} needs reason")
    return issues


def _decision_attestation_issues(decision: dict[str, Any]) -> list[str]:
    """Validate optional trust metadata without invalidating legacy records."""
    attestation = decision.get("attestation")
    if attestation is None:
        return []
    if not isinstance(attestation, dict):
        return ["Decision attestation must be an object"]
    issues: list[str] = []
    if attestation.get("type") != "human-decision-attestation":
        issues.append("Decision attestation has an invalid type")
    if attestation.get("reported_actor") != decision.get("decided_by"):
        issues.append("Decision attestation reported_actor must match decided_by")
    if attestation.get("identity_verification") != "not-performed-by-core":
        issues.append(
            "Decision attestation identity_verification must disclose the Core boundary"
        )
    if attestation.get("confirmation_source") != "caller-attested":
        issues.append("Decision attestation has an invalid confirmation_source")
    return issues


def decision_record_issues(
    decision: Any,
    *,
    unit_id: str | None = None,
    scope: str | None = None,
) -> list[str]:
    if not isinstance(decision, dict):
        return ["Decision must be an object"]
    issues: list[str] = []
    missing = sorted(DECISION_REQUIRED_FIELDS - decision.keys())
    if missing:
        issues.append(f"Decision missing fields: {', '.join(missing)}")
    if decision.get("type") != "human-decision":
        issues.append("Decision has an invalid type")
    if decision.get("schema_version") != "1.0.0":
        issues.append("Decision has an unsupported schema_version")
    if unit_id is not None and decision.get("unit_id") != unit_id:
        issues.append("Decision unit_id does not match Unit")
    if not isinstance(decision.get("gate"), str) or decision.get(
        "gate"
    ) not in DECISION_GATES:
        issues.append("Decision has an invalid gate")
    if not isinstance(decision.get("outcome"), str) or decision.get(
        "outcome"
    ) not in DECISION_OUTCOMES:
        issues.append("Decision has an invalid outcome")
    for field in ("id", "summary", "scope", "decided_by"):
        if not isinstance(decision.get(field), str) or not decision.get(field, "").strip():
            issues.append(f"Decision requires a non-empty {field}")
    if scope is not None and decision.get("scope") != scope:
        issues.append("Decision scope does not match Unit")
    if not _is_iso_timestamp(decision.get("decided_at")):
        issues.append("Decision decided_at must be an ISO-8601 timestamp")
    decision_digest = decision.get("decision_digest")
    if not isinstance(decision_digest, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", decision_digest
    ):
        issues.append("Decision decision_digest must be a SHA-256 digest")
    elif decision_digest != decision_record_digest(decision):
        issues.append("Decision digest does not match its record")
    previous_digest = decision.get("previous_decision_digest")
    if previous_digest is not None and not (
        isinstance(previous_digest, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", previous_digest)
    ):
        issues.append(
            "Decision previous_decision_digest must be null or a SHA-256 digest"
        )
    issues.extend(_decision_attestation_issues(decision))
    approval_subject_types = {
        "inception": "execution-envelope",
        "release": "verification-evidence",
        "knowledge": "project-knowledge-candidate",
        "amendment": "unit-amendment",
    }
    expected_subject_type = approval_subject_types.get(str(decision.get("gate")))
    if expected_subject_type is not None and decision.get("outcome") == "approved":
        approval_subject = decision.get("approval_subject")
        if not isinstance(approval_subject, dict):
            issues.append(
                f"approved {decision.get('gate')} Decision requires approval_subject"
            )
        else:
            if approval_subject.get("type") != expected_subject_type:
                issues.append(
                    f"{decision.get('gate')} Decision approval_subject type is invalid"
                )
            if not isinstance(approval_subject.get("id"), str) or not approval_subject.get(
                "id", ""
            ).strip():
                issues.append(
                    f"{decision.get('gate')} Decision approval_subject requires id"
                )
            if not isinstance(approval_subject.get("digest"), str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", approval_subject.get("digest", "")
            ):
                issues.append(
                    f"{decision.get('gate')} Decision approval_subject requires SHA-256 digest"
                )
            if expected_subject_type == "project-knowledge-candidate" and (
                not isinstance(approval_subject.get("reference"), str)
                or not approval_subject.get("reference", "").strip()
            ):
                issues.append(
                    "knowledge Decision approval_subject requires candidate reference"
                )
    artifact_snapshot = decision.get("artifact_snapshot")
    if artifact_snapshot is not None:
        issues.extend(
            artifact_snapshot_issues(artifact_snapshot, str(decision.get("gate")))
        )
    issues.extend(decision_packet_issues(decision))
    return issues


def decision_ledger_issues(
    decisions: Any,
    *,
    unit_id: str | None = None,
    scope: str | None = None,
) -> list[str]:
    """Validate Decision records and their append-only digest chain."""
    if not isinstance(decisions, dict):
        return ["decisions.json must be an object"]
    issues: list[str] = []
    if unit_id is not None and decisions.get("unit_id") != unit_id:
        issues.append("decisions.json unit_id does not match Unit")
    entries = decisions.get("decisions")
    if not isinstance(entries, list):
        return issues + ["decisions.json decisions must be a list"]

    seen_ids: set[str] = set()
    previous_digest: str | None = None
    previous_decided_at: datetime | None = None
    for index, decision in enumerate(entries):
        issues.extend(
            f"decision {index}: {issue}"
            for issue in decision_record_issues(
                decision,
                unit_id=unit_id,
                scope=scope,
            )
        )
        if not isinstance(decision, dict):
            continue
        decision_id = decision.get("id")
        if isinstance(decision_id, str):
            if decision_id in seen_ids:
                issues.append(f"decision {index}: duplicate Decision id")
            seen_ids.add(decision_id)
        if decision.get("previous_decision_digest") != previous_digest:
            issues.append(
                f"decision {index}: Decision does not continue the digest chain"
            )
        decided_at = _parse_iso_timestamp(decision.get("decided_at"))
        if (
            decided_at is not None
            and previous_decided_at is not None
            and decided_at <= previous_decided_at
        ):
            issues.append(
                f"decision {index}: decided_at must be later than the previous Decision"
            )
        if decided_at is not None:
            previous_decided_at = decided_at
        digest = decision.get("decision_digest")
        previous_digest = digest if isinstance(digest, str) else None
    return issues

def latest_decision(decisions: dict[str, Any], gate: str) -> dict[str, Any] | None:
    entries = decisions.get("decisions", [])
    if not isinstance(entries, list):
        return None
    for entry in reversed(entries):
        if isinstance(entry, dict) and entry.get("gate") == gate:
            return entry
    return None


def has_approved_decision(
    decisions: dict[str, Any],
    gate: str,
    *,
    unit_id: str | None = None,
    scope: str | None = None,
) -> bool:
    if decision_ledger_issues(decisions, unit_id=unit_id, scope=scope):
        return False
    latest = latest_decision(decisions, gate)
    return (
        latest is not None
        and latest.get("outcome") == "approved"
        and not decision_record_issues(latest, unit_id=unit_id, scope=scope)
    )


__all__ = [
    "ALLOWED_TRANSITIONS",
    "DECISION_ALLOWED_STATUSES",
    "DECISION_GATES",
    "DECISION_OUTCOMES",
    "DECISION_PACKET_FIELDS",
    "DECISION_PACKET_VERSION",
    "DECISION_REQUIRED_FIELDS",
    "LIFECYCLE_STATUSES",
    "REQUIRED_DECISIONS_FOR_TRANSITIONS",
    "STATUS_PHASE",
    "TERMINAL_STATUSES",
    "decision_ledger_issues",
    "decision_packet_issues",
    "decision_record_digest",
    "decision_record_issues",
    "has_approved_decision",
    "latest_decision",
]
