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
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


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
        "connector_contract",
        "scope",
        "maximum_executions",
        "status",
        "knowledge_context",
        "created_by",
        "created_at",
        "updated_at",
    }
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required)
    if missing:
        issues.append("engagement missing fields: " + ", ".join(missing))
    if unknown:
        issues.append("engagement contains unsupported fields: " + ", ".join(unknown))
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
    connector = value.get("connector_contract")
    connector_fields = {
        "id",
        "kind",
        "transport",
        "endpoint_ref",
        "auth_ref",
        "allowed_operations",
        "maximum_action_level",
    }
    if not isinstance(connector, dict):
        issues.append("engagement connector_contract must be an object")
    else:
        missing_connector = sorted(connector_fields - connector.keys())
        unknown_connector = sorted(connector.keys() - connector_fields)
        if missing_connector:
            issues.append(
                "engagement connector_contract missing fields: "
                + ", ".join(missing_connector)
            )
        if unknown_connector:
            issues.append(
                "engagement connector_contract contains unsupported fields: "
                + ", ".join(unknown_connector)
            )
        if connector.get("id") != value.get("connector_id"):
            issues.append("engagement connector_contract id does not match")
        if connector.get("kind") != "nahonza" or connector.get("transport") != "agent-api":
            issues.append("engagement connector_contract has an invalid connector type")
        for field in ("endpoint_ref", "auth_ref"):
            reference = connector.get(field)
            if not isinstance(reference, str) or not reference.startswith("env://"):
                issues.append(f"engagement connector_contract requires {field}")
        operations = connector.get("allowed_operations")
        if (
            not isinstance(operations, list)
            or not operations
            or any(not isinstance(item, str) or not item for item in operations)
            or len(operations) != len(set(operations))
        ):
            issues.append(
                "engagement connector_contract allowed_operations must be unique strings"
            )
        elif value.get("operation") not in operations:
            issues.append("engagement operation is not allowed by connector_contract")
        if connector.get("maximum_action_level") != value.get("action_level"):
            issues.append("engagement connector_contract action level does not match")
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
    ledger_fields = {"type", "schema_version", "engagement_id", "executions"}
    missing_ledger = sorted(ledger_fields - value.keys())
    unknown_ledger = sorted(value.keys() - ledger_fields)
    if missing_ledger:
        issues.append("execution ledger missing fields: " + ", ".join(missing_ledger))
    if unknown_ledger:
        issues.append(
            "execution ledger contains unsupported fields: "
            + ", ".join(unknown_ledger)
        )
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
    previous_digest: str | None = None
    seen_ids: set[str] = set()
    execution_fields = {
        "id",
        "status",
        "requested_by",
        "scope",
        "prior_execution_ids",
        "request_digest",
        "authorization_digest",
        "remote_task_id",
        "phase",
        "result_digest",
        "result_receipt_digest",
        "error",
        "created_at",
        "updated_at",
        "previous_execution_digest",
        "execution_digest",
    }
    for index, execution in enumerate(executions):
        if not isinstance(execution, dict):
            issues.append(f"execution {index} must be an object")
            continue
        missing = sorted(execution_fields - execution.keys())
        unknown = sorted(execution.keys() - execution_fields)
        if missing:
            issues.append(f"execution {index} missing fields: {', '.join(missing)}")
        if unknown:
            issues.append(
                f"execution {index} contains unsupported fields: {', '.join(unknown)}"
            )
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
        requested_by = execution.get("requested_by")
        if not isinstance(requested_by, str) or not requested_by.strip():
            issues.append(f"execution {index} requires requested_by")
        scope = execution.get("scope")
        if (
            not isinstance(scope, list)
            or not scope
            or any(not isinstance(item, str) or not item.strip() for item in scope)
            or len(scope) != len(set(scope))
        ):
            issues.append(f"execution {index} scope must be unique non-empty strings")
        prior = execution.get("prior_execution_ids")
        if (
            not isinstance(prior, list)
            or any(not isinstance(item, str) or not item for item in prior)
            or len(prior) != len(set(prior))
        ):
            issues.append(f"execution {index} prior_execution_ids must be unique strings")
        elif not set(prior).issubset(seen_ids):
            issues.append(f"execution {index} references a non-prior execution")
        for field in ("created_at", "updated_at"):
            if not is_timestamp(execution.get(field)):
                issues.append(f"execution {index} requires {field}")
        remote_task_id = execution.get("remote_task_id")
        if remote_task_id is not None and (
            not isinstance(remote_task_id, str) or not remote_task_id.strip()
        ):
            issues.append(f"execution {index} has an invalid remote_task_id")
        phase = execution.get("phase")
        if phase is not None and (not isinstance(phase, str) or not phase.strip()):
            issues.append(f"execution {index} has an invalid phase")
        error = execution.get("error")
        if error is not None and not isinstance(error, str):
            issues.append(f"execution {index} has an invalid error")
        result_digest = execution.get("result_digest")
        receipt_digest = execution.get("result_receipt_digest")
        if status in {"queued", "running", "completed"} and not isinstance(
            remote_task_id, str
        ):
            issues.append(f"execution {index} status {status} requires remote_task_id")
        if status in {"dispatching", "uncertain"} and remote_task_id is not None:
            issues.append(f"execution {index} status {status} forbids remote_task_id")
        if status == "completed":
            for field, digest in (
                ("result_digest", result_digest),
                ("result_receipt_digest", receipt_digest),
            ):
                if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
                    issues.append(f"execution {index} completed status requires {field}")
            if error is not None:
                issues.append(f"execution {index} completed status forbids error")
        elif result_digest is not None or receipt_digest is not None:
            issues.append(
                f"execution {index} non-completed status forbids result digests"
            )
        if execution.get("previous_execution_digest") != previous_digest:
            issues.append(f"execution {index} does not continue the digest chain")
        digest = execution.get("execution_digest")
        expected_digest = canonical_digest(
            {key: item for key, item in execution.items() if key != "execution_digest"}
        )
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            issues.append(f"execution {index} requires execution_digest")
        elif digest != expected_digest:
            issues.append(f"execution {index} digest does not match")
        else:
            previous_digest = digest
        if isinstance(execution_id, str) and execution_id:
            seen_ids.add(execution_id)
    if len(identifiers) != len(set(identifiers)):
        issues.append("execution ledger contains duplicate IDs")
    if in_flight > 1:
        issues.append("execution ledger permits only one in-flight execution")
    return issues


def result_receipt_issues(
    value: Any,
    *,
    engagement: dict[str, Any],
    execution: dict[str, Any],
) -> list[str]:
    if not isinstance(value, dict):
        return ["result receipt must be an object"]
    required = {
        "type",
        "schema_version",
        "engagement_id",
        "execution_id",
        "connector_id",
        "remote_task_id",
        "request_digest",
        "result",
        "received_at",
        "result_digest",
        "receipt_digest",
    }
    issues: list[str] = []
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required)
    if missing:
        issues.append("result receipt missing fields: " + ", ".join(missing))
    if unknown:
        issues.append("result receipt contains unsupported fields: " + ", ".join(unknown))
    expected = {
        "type": "agent-control-result-receipt",
        "schema_version": SCHEMA_VERSION,
        "engagement_id": engagement.get("id"),
        "execution_id": execution.get("id"),
        "connector_id": engagement.get("connector_id"),
        "remote_task_id": execution.get("remote_task_id"),
        "request_digest": execution.get("request_digest"),
    }
    for field, expected_value in expected.items():
        if value.get(field) != expected_value:
            issues.append(f"result receipt {field} does not match")
    if not is_timestamp(value.get("received_at")):
        issues.append("result receipt requires received_at")
    result_digest = value.get("result_digest")
    if result_digest != canonical_digest(value.get("result")):
        issues.append("result receipt result_digest does not match")
    if result_digest != execution.get("result_digest"):
        issues.append("result receipt digest does not match execution")
    receipt_digest = value.get("receipt_digest")
    expected_receipt_digest = canonical_digest(
        {key: item for key, item in value.items() if key != "receipt_digest"}
    )
    if receipt_digest != expected_receipt_digest:
        issues.append("result receipt receipt_digest does not match")
    if receipt_digest != execution.get("result_receipt_digest"):
        issues.append("result receipt binding does not match execution")
    return issues
