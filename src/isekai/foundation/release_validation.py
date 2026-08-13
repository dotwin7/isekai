from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .evaluation import evaluate_all_evaluations
from .records import (
    foundation_decision_history_issues as _foundation_decision_history_issues,
    foundation_evidence_digest as _foundation_evidence_digest,
)
from .types import (
    FOUNDATION_CHECK_FIELDS,
    FOUNDATION_EVIDENCE_FIELDS,
    FoundationError,
    FoundationRelease,
)
from .validation import (
    load_json as _load_json,
    optional_json as _optional_json,
    parse_timestamp as _parse_timestamp,
    validate_provenance_record as _validate_provenance_record,
)


def latest_foundation_decision(
    foundation: FoundationRelease,
) -> tuple[dict[str, Any] | None, list[str]]:
    path = foundation.root / "decisions.json"
    if not path.is_file():
        return None, ["missing Foundation release Decision"]
    try:
        document = _load_json(path, root=foundation.root)
    except FoundationError as exc:
        return None, [str(exc)]
    if document.get("foundation_id") != foundation.manifest["id"]:
        return None, ["Foundation Decision foundation_id does not match release"]
    if document.get("version") != foundation.version:
        return None, ["Foundation Decision document version does not match release"]
    entries = document.get("decisions")
    if not isinstance(entries, list) or not entries:
        return None, ["Foundation release Decision list is empty"]
    issues = _foundation_decision_history_issues(
        foundation,
        entries,
        require_latest_approval=True,
    )
    latest = entries[-1]
    return (latest if isinstance(latest, dict) else None), issues


def foundation_evidence_issues(
    foundation: FoundationRelease,
    evidence: dict[str, Any] | None,
    *,
    evaluations: dict[str, Any] | None = None,
) -> list[str]:
    if evidence is None:
        return ["missing Foundation release Evidence"]
    issues: list[str] = []
    missing = sorted(FOUNDATION_EVIDENCE_FIELDS - evidence.keys())
    if missing:
        issues.append(f"Foundation Evidence missing fields: {', '.join(missing)}")
    if evidence.get("foundation_id") != foundation.manifest["id"]:
        issues.append("Foundation Evidence foundation_id does not match release")
    if evidence.get("version") != foundation.version:
        issues.append("Foundation Evidence version does not match release")
    if evidence.get("approval_digest") != foundation.approval_digest:
        issues.append("Foundation Evidence approval_digest does not match release content")
    if evidence.get("type") != "foundation-release-evidence":
        issues.append("Foundation Evidence has an invalid type")
    if evidence.get("schema_version") != "1.0.0":
        issues.append("Foundation Evidence has an unsupported schema_version")
    evidence_digest = evidence.get("evidence_digest")
    if not isinstance(evidence_digest, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", evidence_digest
    ):
        issues.append("Foundation Evidence evidence_digest must be a SHA-256 digest")
    elif evidence_digest != _foundation_evidence_digest(evidence):
        issues.append("Foundation Evidence digest does not match its record")
    if evidence.get("passed") is not True:
        issues.append("Foundation release Evidence is not passing")
    if not isinstance(evidence.get("scope"), str) or not evidence.get("scope", "").strip():
        issues.append("Foundation Evidence requires a scope")
    if not isinstance(evidence.get("recorded_by"), str) or not evidence.get("recorded_by", "").strip():
        issues.append("Foundation Evidence requires recorded_by provenance")
    attestation = evidence.get("attestation")
    if attestation is not None:
        if not isinstance(attestation, dict):
            issues.append("Foundation Evidence attestation must be an object")
        else:
            if attestation.get("type") != "local-evaluation-attestation":
                issues.append("Foundation Evidence attestation has an invalid type")
            if attestation.get("reported_actor") != evidence.get("recorded_by"):
                issues.append(
                    "Foundation Evidence attestation reported_actor must match recorded_by"
                )
            if attestation.get("execution_verification") != "not-performed-by-core":
                issues.append(
                    "Foundation Evidence attestation must disclose the Core execution boundary"
                )
            if attestation.get("identity_verification") != "not-performed-by-core":
                issues.append(
                    "Foundation Evidence attestation must disclose the Core identity boundary"
                )
    if not isinstance(evidence.get("id"), str) or not evidence.get("id", "").strip():
        issues.append("Foundation Evidence requires a non-empty id")
    recorded_at: datetime | None = None
    try:
        recorded_at = _parse_timestamp(
            evidence.get("recorded_at"), "Foundation Evidence recorded_at"
        )
    except FoundationError as exc:
        issues.append(str(exc))
    checks = evidence.get("checks")
    if not isinstance(checks, list) or not checks:
        issues.append("Foundation Evidence has no checks")
    else:
        seen_check_ids: set[str] = set()
        for index, check in enumerate(checks):
            if not isinstance(check, dict):
                issues.append(f"Foundation Evidence check {index} must be an object")
                continue
            missing_check = sorted(FOUNDATION_CHECK_FIELDS - check.keys())
            if missing_check:
                issues.append(
                    f"Foundation Evidence check {index} missing fields: "
                    f"{', '.join(missing_check)}"
                )
            if check.get("passed") is not True:
                issues.append(f"Foundation Evidence check {index} is not passing")
            check_id = check.get("id")
            if not isinstance(check_id, str) or not check_id.strip():
                issues.append(f"Foundation Evidence check {index} requires a non-empty id")
            elif check_id in seen_check_ids:
                issues.append(f"Foundation Evidence has duplicate check id: {check_id}")
            else:
                seen_check_ids.add(check_id)
            if not isinstance(check.get("details"), str) or not check.get("details", "").strip():
                issues.append(f"Foundation Evidence check {index} requires details")
            try:
                _validate_provenance_record(check.get("provenance"), f"Foundation Evidence check {index} provenance")
                check_recorded_at = _parse_timestamp(
                    check["provenance"].get("recorded_at"),
                    f"Foundation Evidence check {index} provenance recorded_at",
                )
                if recorded_at is not None and check_recorded_at > recorded_at:
                    issues.append(
                        f"Foundation Evidence check {index} provenance recorded_at "
                        "is after Evidence recorded_at"
                    )
            except FoundationError as exc:
                issues.append(str(exc))
        try:
            graded = evaluations if evaluations is not None else evaluate_all_evaluations(foundation)
            expected = set(graded["evaluations"])
            actual = {check.get("id") for check in checks if isinstance(check, dict)}
            missing_groups = sorted(expected - actual)
            if missing_groups:
                issues.append("Foundation Evidence missing evaluation checks: " + ", ".join(missing_groups))
        except FoundationError as exc:
            issues.append(str(exc))
    return issues


def approval_blockers(
    foundation: FoundationRelease,
    *,
    evaluations: dict[str, Any] | None = None,
) -> list[str]:
    decision, decision_issues = latest_foundation_decision(foundation)
    blockers = list(decision_issues)
    if decision is not None and decision.get("outcome") != "approved":
        blockers.append("latest Foundation release Decision is not approved")
    evidence_path = foundation.root / "evidence/release.json"
    try:
        evidence = _optional_json(evidence_path, root=foundation.root)
    except FoundationError as exc:
        evidence = None
        blockers.append(str(exc))
    graded = evaluations
    if graded is None:
        try:
            graded = evaluate_all_evaluations(foundation)
        except FoundationError as exc:
            blockers.append(str(exc))
    blockers.extend(foundation_evidence_issues(foundation, evidence, evaluations=graded))
    if graded is not None:
        for evaluation_id, result in graded["evaluations"].items():
            if result["passed"] is not True:
                blockers.append(f"evaluation group {evaluation_id} did not pass")
    return blockers


__all__ = [
    "approval_blockers",
    "foundation_evidence_issues",
    "latest_foundation_decision",
]
