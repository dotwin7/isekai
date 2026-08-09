from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..errors import IntegrityError
from .authorization import _authorization_ledger_digest, _authorization_ledger_issues
from .common import _unit_json, _unit_path_without_symlinks, _write_json


EXECUTION_AUTHORIZATION_RECORDS_DIR = "execution-authorization-records"
EXECUTION_AUTHORIZATION_RECORD_REQUIRED_FIELDS = {
    "type",
    "schema_version",
    "unit_id",
    "envelope_id",
    "envelope_digest",
    "authorization_ledger_digest",
    "envelope",
    "authorization_ledger",
    "record_digest",
}


def _execution_authorization_record_digest(record: dict[str, Any]) -> str:
    subject = {key: value for key, value in record.items() if key != "record_digest"}
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _execution_authorization_record_relative(envelope_id: str) -> str:
    if re.fullmatch(r"ENV-[A-Za-z0-9-]+", envelope_id) is None:
        raise IntegrityError(
            f"Execution authorization record has an invalid Envelope id: {envelope_id}"
        )
    return f"{EXECUTION_AUTHORIZATION_RECORDS_DIR}/{envelope_id}.json"


def _execution_authorization_record_issues(
    record: Any,
    unit: dict[str, Any],
    *,
    expected_envelope_id: str | None = None,
) -> list[str]:
    if not isinstance(record, dict):
        return ["Execution authorization record must be an object"]
    issues: list[str] = []
    missing = sorted(EXECUTION_AUTHORIZATION_RECORD_REQUIRED_FIELDS - record.keys())
    if missing:
        issues.append(
            "Execution authorization record missing fields: " + ", ".join(missing)
        )
    unexpected = sorted(set(record) - EXECUTION_AUTHORIZATION_RECORD_REQUIRED_FIELDS)
    if unexpected:
        issues.append(
            "Execution authorization record has unsupported fields: "
            + ", ".join(unexpected)
        )
    if record.get("type") != "execution-authorization-record":
        issues.append("Execution authorization record has an invalid type")
    if record.get("schema_version") != "1.0.0":
        issues.append("Execution authorization record has an unsupported schema_version")
    if record.get("unit_id") != unit.get("id"):
        issues.append("Execution authorization record unit_id does not match Unit")
    envelope_id = record.get("envelope_id")
    if not isinstance(envelope_id, str) or re.fullmatch(
        r"ENV-[A-Za-z0-9-]+", envelope_id
    ) is None:
        issues.append("Execution authorization record has an invalid envelope_id")
    elif expected_envelope_id is not None and envelope_id != expected_envelope_id:
        issues.append("Execution authorization record id does not match its path")

    envelope = record.get("envelope")
    ledger = record.get("authorization_ledger")
    if not isinstance(envelope, dict):
        issues.append("Execution authorization record envelope must be an object")
    else:
        if record.get("envelope_id") != envelope.get("id"):
            issues.append("Execution authorization record does not match its Envelope")
        if record.get("envelope_digest") != envelope.get("approval_digest"):
            issues.append("Execution authorization record Envelope digest is invalid")
    if not isinstance(ledger, dict):
        issues.append(
            "Execution authorization record authorization_ledger must be an object"
        )
    elif isinstance(envelope, dict):
        issues.extend(
            "archived " + issue
            for issue in _authorization_ledger_issues(ledger, unit, envelope)
        )
        if record.get("authorization_ledger_digest") != _authorization_ledger_digest(
            ledger
        ):
            issues.append("Execution authorization record ledger digest is invalid")
    record_digest = record.get("record_digest")
    if not isinstance(record_digest, str) or re.fullmatch(
        r"sha256:[0-9a-f]{64}", record_digest
    ) is None:
        issues.append("Execution authorization record record_digest is invalid")
    elif record_digest != _execution_authorization_record_digest(record):
        issues.append("Execution authorization record digest does not match its record")
    return issues


def _persist_execution_authorization_record(
    unit_dir: Path,
    unit: dict[str, Any],
    envelope: dict[str, Any],
    ledger: dict[str, Any],
) -> Path:
    envelope_id = str(envelope.get("id", ""))
    relative = _execution_authorization_record_relative(envelope_id)
    target = _unit_path_without_symlinks(unit_dir, relative)
    record = {
        "type": "execution-authorization-record",
        "schema_version": "1.0.0",
        "unit_id": unit.get("id"),
        "envelope_id": envelope_id,
        "envelope_digest": envelope.get("approval_digest"),
        "authorization_ledger_digest": _authorization_ledger_digest(ledger),
        "envelope": envelope,
        "authorization_ledger": ledger,
    }
    record["record_digest"] = _execution_authorization_record_digest(record)
    issues = _execution_authorization_record_issues(
        record,
        unit,
        expected_envelope_id=envelope_id,
    )
    if issues:
        raise IntegrityError(
            "Execution authorization archive rejected: " + "; ".join(issues)
        )
    if target.exists() or target.is_symlink():
        existing = _unit_json(unit_dir, relative)
        if existing.get("record_digest") != record["record_digest"]:
            raise IntegrityError(
                "Execution authorization archive conflicts with an existing Envelope id"
            )
        return target
    _write_json(target, record)
    persisted = _unit_json(unit_dir, relative)
    persisted_issues = _execution_authorization_record_issues(
        persisted,
        unit,
        expected_envelope_id=envelope_id,
    )
    if persisted_issues:
        raise IntegrityError(
            "Execution authorization archive postflight failed: "
            + "; ".join(persisted_issues)
        )
    return target
