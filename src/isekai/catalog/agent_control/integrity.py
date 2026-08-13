from __future__ import annotations

from pathlib import Path
from typing import Any

from isekai.workflow.errors import IntegrityError

from .schema import (
    SCHEMA_VERSION,
    SHA256,
    canonical_digest,
    engagement_issues,
    execution_ledger_issues,
    is_timestamp,
    result_receipt_issues,
)
from .storage import read_json


def validate_engagement(value: dict[str, Any]) -> None:
    issues = engagement_issues(value)
    if issues:
        raise IntegrityError("Agent Control engagement is invalid: " + "; ".join(issues))


def validate_ledger(value: dict[str, Any], engagement_id: str) -> None:
    issues = execution_ledger_issues(value, engagement_id=engagement_id)
    if issues:
        raise IntegrityError(
            "Agent Control execution ledger is invalid: " + "; ".join(issues)
        )


def seal_execution(value: dict[str, Any]) -> None:
    value["execution_digest"] = canonical_digest(
        {key: item for key, item in value.items() if key != "execution_digest"}
    )


def validate_result_receipts(
    directory: Path,
    engagement: dict[str, Any],
    ledger: dict[str, Any],
) -> None:
    for execution in ledger["executions"]:
        if execution.get("status") != "completed":
            continue
        execution_id = str(execution.get("id"))
        relative = Path("results") / f"{execution_id}.json"
        try:
            receipt = read_json(
                directory / relative,
                root=directory,
                label=f"Agent Control result receipt {execution_id}",
            )
        except FileNotFoundError as exc:
            raise IntegrityError(
                f"Agent Control result receipt is missing: {execution_id}"
            ) from exc
        issues = result_receipt_issues(
            receipt,
            engagement=engagement,
            execution=execution,
        )
        if issues:
            raise IntegrityError(
                "Agent Control result receipt is invalid: " + "; ".join(issues)
            )


def approved_contract(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in (
            "id",
            "project_id",
            "objective",
            "connector_id",
            "operation",
            "action_level",
            "connector_contract",
            "scope",
            "maximum_executions",
            "knowledge_context",
        )
    }


def validate_approval(approval: dict[str, Any], engagement: dict[str, Any]) -> None:
    approval_fields = {
        "type",
        "schema_version",
        "engagement_id",
        "outcome",
        "summary",
        "decided_by",
        "approved_at",
        "contract_digest",
        "approval_digest",
    }
    if set(approval) != approval_fields:
        raise IntegrityError("Agent Control approval has unsupported or missing fields")
    if (
        approval.get("type") != "agent-control-engagement-approval"
        or approval.get("schema_version") != SCHEMA_VERSION
        or approval.get("engagement_id") != engagement.get("id")
        or approval.get("outcome") != "approved"
    ):
        raise IntegrityError("Agent Control approval contract is invalid")
    for field in ("summary", "decided_by"):
        if not isinstance(approval.get(field), str) or not approval[field].strip():
            raise IntegrityError(f"Agent Control approval requires {field}")
    if not is_timestamp(approval.get("approved_at")):
        raise IntegrityError("Agent Control approval requires approved_at")
    contract_digest = approval.get("contract_digest")
    if not isinstance(contract_digest, str) or SHA256.fullmatch(contract_digest) is None:
        raise IntegrityError("Agent Control approval requires contract_digest")
    recorded_digest = approval.get("approval_digest")
    if not isinstance(recorded_digest, str) or recorded_digest != canonical_digest(
        {key: item for key, item in approval.items() if key != "approval_digest"}
    ):
        raise IntegrityError("Agent Control approval digest does not match")
    if contract_digest != canonical_digest(approved_contract(engagement)):
        raise IntegrityError("Agent Control approved contract has changed")
