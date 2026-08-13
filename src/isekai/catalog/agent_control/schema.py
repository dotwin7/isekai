from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any


SCHEMA_VERSION = "1.0.0"
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
ENGAGEMENT_STATUSES = {"proposed", "active", "completed", "cancelled"}
EXECUTION_STATUSES = {
    "dispatching",
    "queued",
    "running",
    "completed",
    "failed",
    "uncertain",
}
IN_FLIGHT_STATUSES = {"dispatching", "queued", "running"}


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def is_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def engagement_issues(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["engagement must be an object"]
    issues: list[str] = []
    required = {
        "type",
        "schema_version",
        "id",
        "project_id",
        "title",
        "objective",
        "connector_id",
        "operation",
        "action_level",
        "scope",
        "maximum_executions",
        "status",
        "knowledge_context",
        "created_by",
        "created_at",
        "updated_at",
    }
    missing = sorted(required - value.keys())
    if missing:
        issues.append("engagement missing fields: " + ", ".join(missing))
    if value.get("type") != "agent-control-engagement":
        issues.append("engagement has an invalid type")
    if value.get("schema_version") != SCHEMA_VERSION:
        issues.append("engagement has an unsupported schema_version")
    for field in (
        "id",
        "project_id",
        "title",
        "objective",
        "connector_id",
        "operation",
        "action_level",
        "created_by",
    ):
        if not isinstance(value.get(field), str) or not value.get(field, "").strip():
            issues.append(f"engagement requires {field}")
    if value.get("action_level") not in {"L0", "L1", "L2"}:
        issues.append("engagement action_level must be L0, L1, or L2")
    scope = value.get("scope")
    if (
        not isinstance(scope, list)
        or not scope
        or any(not isinstance(item, str) or not item.strip() for item in scope)
        or len(scope) != len(set(scope))
    ):
        issues.append("engagement scope must be a unique non-empty string list")
    maximum = value.get("maximum_executions")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 100:
        issues.append("engagement maximum_executions must be between 1 and 100")
    if value.get("status") not in ENGAGEMENT_STATUSES:
        issues.append("engagement has an invalid status")
    if value.get("knowledge_context") is not None and not isinstance(
        value.get("knowledge_context"), dict
    ):
        issues.append("engagement knowledge_context must be an object or null")
    for field in ("created_at", "updated_at"):
        if not is_timestamp(value.get(field)):
            issues.append(f"engagement {field} must be an ISO-8601 timestamp")
    return issues


def execution_ledger_issues(value: Any, *, engagement_id: str) -> list[str]:
    if not isinstance(value, dict):
        return ["execution ledger must be an object"]
    issues: list[str] = []
    if value.get("type") != "agent-control-execution-ledger":
        issues.append("execution ledger has an invalid type")
    if value.get("schema_version") != SCHEMA_VERSION:
        issues.append("execution ledger has an unsupported schema_version")
    if value.get("engagement_id") != engagement_id:
        issues.append("execution ledger engagement_id does not match")
    executions = value.get("executions")
    if not isinstance(executions, list):
        return issues + ["execution ledger executions must be a list"]
    identifiers: list[str] = []
    in_flight = 0
    for index, execution in enumerate(executions):
        if not isinstance(execution, dict):
            issues.append(f"execution {index} must be an object")
            continue
        execution_id = execution.get("id")
        if not isinstance(execution_id, str) or not execution_id:
            issues.append(f"execution {index} requires id")
        else:
            identifiers.append(execution_id)
        status = execution.get("status")
        if status not in EXECUTION_STATUSES:
            issues.append(f"execution {index} has an invalid status")
        if status in IN_FLIGHT_STATUSES:
            in_flight += 1
        for field in ("request_digest", "authorization_digest"):
            digest = execution.get(field)
            if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
                issues.append(f"execution {index} requires {field}")
        if not is_timestamp(execution.get("created_at")):
            issues.append(f"execution {index} requires created_at")
    if len(identifiers) != len(set(identifiers)):
        issues.append("execution ledger contains duplicate IDs")
    if in_flight > 1:
        issues.append("execution ledger permits only one in-flight execution")
    return issues
