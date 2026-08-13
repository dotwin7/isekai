from __future__ import annotations

from typing import Any, Callable, Mapping

from isekai.runtime.catalog_actions import active_catalog_action
from isekai.runtime.request_fields import (
    RuntimeContractError,
    string_field,
    string_list_field,
)

from .service import (
    approve_engagement,
    create_engagement,
    engagement_status,
    poll_execution,
    start_execution,
)


def _positive_integer(values: Mapping[str, Any], field: str) -> int:
    value = values.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RuntimeContractError(
            f"runtime request field {field} must be a positive integer"
        )
    return value


def _create(values: Mapping[str, Any]) -> dict[str, Any]:
    return create_engagement(
        string_field(values, "project", default="."),
        title=string_field(values, "title"),
        objective=string_field(values, "objective"),
        connector_id=string_field(values, "connector_id"),
        operation=string_field(values, "operation"),
        scope=string_list_field(values, "scope"),
        maximum_executions=_positive_integer(values, "maximum_executions"),
        created_by=string_field(values, "created_by"),
        knowledge_entry_ids=string_list_field(values, "knowledge_entry_ids"),
    )


def _approve(values: Mapping[str, Any]) -> dict[str, Any]:
    return approve_engagement(
        string_field(values, "engagement"),
        decided_by=string_field(values, "decided_by"),
        summary=string_field(values, "summary"),
    )


def _status(values: Mapping[str, Any]) -> dict[str, Any]:
    return engagement_status(string_field(values, "engagement"))


def _execute(values: Mapping[str, Any]) -> dict[str, Any]:
    return start_execution(
        string_field(values, "engagement"),
        prompt=string_field(values, "prompt"),
        scope=string_list_field(values, "scope"),
        requested_by=string_field(values, "requested_by"),
        prior_execution_ids=string_list_field(values, "prior_execution_ids"),
    )


def _poll(values: Mapping[str, Any]) -> dict[str, Any]:
    return poll_execution(
        string_field(values, "engagement"),
        execution_id=string_field(values, "execution_id"),
    )


Handler = Callable[[Mapping[str, Any]], dict[str, Any]]
_ENTRY = "agent-control"
ACTION_HANDLERS: dict[str, Handler] = {
    name: active_catalog_action(_ENTRY, name, handler)
    for name, handler in {
        "agent-engagement-create": _create,
        "agent-engagement-approve": _approve,
        "agent-engagement-status": _status,
        "agent-execution-start": _execute,
        "agent-execution-status": _poll,
    }.items()
}
