from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from isekai.support.jsonio import ensure_directory_beneath
from isekai.workflow.errors import IntegrityError, WorkflowError
from isekai.workflow.project import load_project
from isekai.workflow.project_knowledge import current_project_knowledge

from .connectors import Connector, ConnectorRequest, connector_from_project_config
from .connectors.nahonza import ConnectorTransportError
from .schema import (
    IN_FLIGHT_STATUSES,
    SCHEMA_VERSION,
    SHA256,
    canonical_digest,
    engagement_issues,
    execution_ledger_issues,
    is_timestamp,
)
from .storage import (
    APPROVAL_FILE,
    ENGAGEMENT_FILE,
    ENGAGEMENTS_DIRECTORY,
    EXECUTIONS_FILE,
    engagement_lock,
    load_engagement,
    load_executions,
    read_json,
    resolve_engagement_directory,
    write_json,
)


ConnectorFactory = Callable[[dict[str, Any]], Connector]
_ACTION_LEVELS = {"L0": 0, "L1": 1, "L2": 2}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def _project(path: str | Path) -> tuple[Path, dict[str, Any]]:
    manifest, project, _foundation, _extensions = load_project(path)
    return manifest, project


def _connectors(project: dict[str, Any]) -> list[dict[str, Any]]:
    agent_control = project.get("agent_control")
    if not isinstance(agent_control, dict):
        raise WorkflowError("Project does not configure agent_control")
    connectors = agent_control.get("connectors")
    if not isinstance(connectors, list):
        raise WorkflowError("Project agent_control.connectors must be a list")
    normalized: list[dict[str, Any]] = []
    identifiers: list[str] = []
    for index, connector in enumerate(connectors):
        if not isinstance(connector, dict):
            raise WorkflowError(f"Project Agent Control connector {index} must be an object")
        connector_id = connector.get("id")
        if not isinstance(connector_id, str) or not connector_id.strip():
            raise WorkflowError(f"Project Agent Control connector {index} requires id")
        identifiers.append(connector_id)
        operations = connector.get("allowed_operations")
        action_level = connector.get("maximum_action_level")
        if (
            connector.get("kind") != "nahonza"
            or connector.get("transport") != "agent-api"
            or not isinstance(operations, list)
            or not operations
            or any(not isinstance(item, str) or not item for item in operations)
            or action_level not in _ACTION_LEVELS
        ):
            raise WorkflowError(f"Project Agent Control connector {connector_id} is invalid")
        for field in ("endpoint_ref", "auth_ref"):
            if not isinstance(connector.get(field), str) or not str(connector[field]).startswith(
                "env://"
            ):
                raise WorkflowError(
                    f"Project Agent Control connector {connector_id} {field} must be env://"
                )
        normalized.append(copy.deepcopy(connector))
    if len(identifiers) != len(set(identifiers)):
        raise WorkflowError("Project Agent Control connectors must have unique IDs")
    return normalized


def _connector_config(
    project: dict[str, Any], connector_id: str, operation: str
) -> dict[str, Any]:
    connector = next(
        (item for item in _connectors(project) if item.get("id") == connector_id),
        None,
    )
    if connector is None:
        raise WorkflowError(f"Project does not configure connector: {connector_id}")
    if operation not in connector["allowed_operations"]:
        raise WorkflowError(
            f"connector {connector_id} does not allow operation: {operation}"
        )
    return connector


def _knowledge_context(
    project_root: Path,
    project_id: str,
    entry_ids: list[str],
) -> dict[str, Any] | None:
    if not entry_ids:
        return None
    if len(entry_ids) != len(set(entry_ids)) or any(
        not isinstance(item, str) or not item for item in entry_ids
    ):
        raise WorkflowError("knowledge_entry_ids must be unique non-empty strings")
    release = current_project_knowledge(project_root, project_id)
    if release is None:
        raise WorkflowError("Project Knowledge has no approved release")
    by_id = {
        str(entry.get("id")): entry
        for entry in release.get("entries", [])
        if isinstance(entry, dict) and entry.get("status") == "approved"
    }
    missing = sorted(set(entry_ids) - by_id.keys())
    if missing:
        raise WorkflowError(
            "Project Knowledge entries are not approved in the current release: "
            + ", ".join(missing)
        )
    entries = [
        {
            key: by_id[entry_id].get(key)
            for key in ("id", "kind", "title", "statement")
        }
        for entry_id in entry_ids
    ]
    context = {
        "type": "agent-control-knowledge-context",
        "release_id": release.get("id"),
        "release_digest": release.get("release_digest"),
        "entries": entries,
    }
    context["context_digest"] = canonical_digest(context)
    return context


def _validate_engagement(value: dict[str, Any]) -> None:
    issues = engagement_issues(value)
    if issues:
        raise IntegrityError("Agent Control engagement is invalid: " + "; ".join(issues))


def _validate_ledger(value: dict[str, Any], engagement_id: str) -> None:
    issues = execution_ledger_issues(value, engagement_id=engagement_id)
    if issues:
        raise IntegrityError(
            "Agent Control execution ledger is invalid: " + "; ".join(issues)
        )


def _approved_contract(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in (
            "id",
            "project_id",
            "objective",
            "connector_id",
            "operation",
            "action_level",
            "scope",
            "maximum_executions",
            "knowledge_context",
        )
    }


def _validate_approval(
    approval: dict[str, Any], engagement: dict[str, Any]
) -> None:
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
    if contract_digest != canonical_digest(_approved_contract(engagement)):
        raise IntegrityError("Agent Control approved contract has changed")


def create_engagement(
    project: str | Path,
    *,
    title: str,
    objective: str,
    connector_id: str,
    operation: str,
    scope: list[str],
    maximum_executions: int,
    created_by: str,
    knowledge_entry_ids: list[str] | None = None,
) -> dict[str, Any]:
    manifest, project_value = _project(project)
    connector = _connector_config(project_value, connector_id, operation)
    project_level = str(project_value.get("maximum_agent_level", "L0"))
    connector_level = str(connector["maximum_action_level"])
    if _ACTION_LEVELS[connector_level] > _ACTION_LEVELS.get(project_level, -1):
        raise WorkflowError(
            f"connector action level {connector_level} exceeds Project {project_level}"
        )
    now = _now()
    engagement_id = _new_id("ENG")
    knowledge = _knowledge_context(
        manifest.parent,
        str(project_value["id"]),
        list(knowledge_entry_ids or []),
    )
    value = {
        "type": "agent-control-engagement",
        "schema_version": SCHEMA_VERSION,
        "id": engagement_id,
        "project_id": str(project_value["id"]),
        "title": title,
        "objective": objective,
        "connector_id": connector_id,
        "operation": operation,
        "action_level": connector_level,
        "scope": list(scope),
        "maximum_executions": maximum_executions,
        "status": "proposed",
        "knowledge_context": knowledge,
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }
    _validate_engagement(value)
    relative = Path(ENGAGEMENTS_DIRECTORY) / engagement_id
    ensure_directory_beneath(manifest.parent, relative, mode=0o700)
    directory = manifest.parent / relative
    write_json(directory, ENGAGEMENT_FILE, value)
    ledger = {
        "type": "agent-control-execution-ledger",
        "schema_version": SCHEMA_VERSION,
        "engagement_id": engagement_id,
        "executions": [],
    }
    _validate_ledger(ledger, engagement_id)
    write_json(directory, EXECUTIONS_FILE, ledger)
    return {"engagement": value, "path": str(directory)}


def approve_engagement(
    engagement: str | Path,
    *,
    decided_by: str,
    summary: str,
) -> dict[str, Any]:
    directory = resolve_engagement_directory(engagement)
    with engagement_lock(directory):
        value = load_engagement(directory)
        _validate_engagement(value)
        if value["status"] != "proposed":
            raise WorkflowError("only a proposed engagement can be approved")
        approved_contract = _approved_contract(value)
        approval = {
            "type": "agent-control-engagement-approval",
            "schema_version": SCHEMA_VERSION,
            "engagement_id": value["id"],
            "outcome": "approved",
            "summary": summary,
            "decided_by": decided_by,
            "approved_at": _now(),
            "contract_digest": canonical_digest(approved_contract),
        }
        approval["approval_digest"] = canonical_digest(approval)
        _validate_approval(approval, value)
        write_json(directory, APPROVAL_FILE, approval)
        value["status"] = "active"
        value["updated_at"] = approval["approved_at"]
        _validate_engagement(value)
        write_json(directory, ENGAGEMENT_FILE, value)
    return {"engagement": value, "approval": approval}


def _approval(
    directory: Path,
    engagement: dict[str, Any],
    *,
    allow_missing: bool = False,
) -> dict[str, Any] | None:
    try:
        approval = read_json(
            directory / APPROVAL_FILE,
            root=directory,
            label="Agent Control engagement approval",
        )
    except FileNotFoundError as exc:
        if allow_missing:
            return None
        raise IntegrityError("Agent Control engagement approval is missing") from exc
    _validate_approval(approval, engagement)
    return approval


def _project_for_engagement(directory: Path, engagement: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    project_root = directory.parent.parent
    manifest, project = _project(project_root / "project.json")
    if project.get("id") != engagement.get("project_id"):
        raise IntegrityError("Agent Control engagement does not match its Project")
    return manifest, project


def start_execution(
    engagement: str | Path,
    *,
    prompt: str,
    scope: list[str],
    requested_by: str,
    prior_execution_ids: list[str] | None = None,
    _connector_factory: ConnectorFactory = connector_from_project_config,
) -> dict[str, Any]:
    directory = resolve_engagement_directory(engagement)
    with engagement_lock(directory):
        engagement_value = load_engagement(directory)
        _validate_engagement(engagement_value)
        if engagement_value["status"] != "active":
            raise WorkflowError("Agent Control engagement must be active")
        approval = _approval(directory, engagement_value)
        if approval is None:  # pragma: no cover - allow_missing is false
            raise IntegrityError("Agent Control engagement approval is missing")
        ledger = load_executions(directory)
        _validate_ledger(ledger, str(engagement_value["id"]))
        executions = ledger["executions"]
        if any(item.get("status") in IN_FLIGHT_STATUSES for item in executions):
            raise WorkflowError("Agent Control engagement already has an in-flight execution")
        if len(executions) >= engagement_value["maximum_executions"]:
            raise WorkflowError("Agent Control engagement execution budget is exhausted")
        if not scope or not set(scope).issubset(set(engagement_value["scope"])):
            raise WorkflowError("execution scope must be a non-empty engagement scope subset")
        prior = list(prior_execution_ids or [])
        known_ids = {str(item.get("id")) for item in executions}
        if len(prior) != len(set(prior)) or not set(prior).issubset(known_ids):
            raise WorkflowError("prior_execution_ids must reference this engagement")
        execution_id = _new_id("EXEC")
        request_contract = {
            "engagement_id": engagement_value["id"],
            "execution_id": execution_id,
            "operation": engagement_value["operation"],
            "prompt": prompt,
            "scope": list(scope),
            "prior_execution_ids": prior,
            "knowledge_context_digest": (
                engagement_value["knowledge_context"].get("context_digest")
                if isinstance(engagement_value.get("knowledge_context"), dict)
                else None
            ),
        }
        execution = {
            "id": execution_id,
            "status": "dispatching",
            "requested_by": requested_by,
            "scope": list(scope),
            "prior_execution_ids": prior,
            "request_digest": canonical_digest(request_contract),
            "authorization_digest": canonical_digest(
                {
                    "approval_digest": approval.get("approval_digest"),
                    "request": request_contract,
                }
            ),
            "remote_task_id": None,
            "phase": None,
            "result_digest": None,
            "error": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        executions.append(execution)
        _validate_ledger(ledger, str(engagement_value["id"]))
        write_json(directory, EXECUTIONS_FILE, ledger)
        _manifest, project_value = _project_for_engagement(directory, engagement_value)
        connector_config = _connector_config(
            project_value,
            str(engagement_value["connector_id"]),
            str(engagement_value["operation"]),
        )

    terminal_status: str
    remote_task_id: str | None
    error: str | None
    try:
        connector = _connector_factory(connector_config)
        handle = connector.start(
            ConnectorRequest(
                execution_id=execution_id,
                operation=str(engagement_value["operation"]),
                prompt=prompt,
                scope=tuple(scope),
                knowledge_context=engagement_value.get("knowledge_context"),
            )
        )
    except ConnectorTransportError as exc:
        terminal_status = "uncertain"
        remote_task_id = None
        error = str(exc)
    except Exception as exc:
        terminal_status = "failed"
        remote_task_id = None
        error = str(exc)
    else:
        terminal_status = handle.status
        remote_task_id = handle.remote_task_id
        error = None

    with engagement_lock(directory):
        ledger = load_executions(directory)
        record = next(item for item in ledger["executions"] if item.get("id") == execution_id)
        if record.get("status") != "dispatching":
            raise IntegrityError("Agent Control execution changed during connector dispatch")
        record["status"] = terminal_status
        record["remote_task_id"] = remote_task_id
        record["error"] = error
        record["updated_at"] = _now()
        _validate_ledger(ledger, str(engagement_value["id"]))
        write_json(directory, EXECUTIONS_FILE, ledger)
    return {"engagement_id": engagement_value["id"], "execution": record}


def poll_execution(
    engagement: str | Path,
    *,
    execution_id: str,
    _connector_factory: ConnectorFactory = connector_from_project_config,
) -> dict[str, Any]:
    directory = resolve_engagement_directory(engagement)
    with engagement_lock(directory):
        engagement_value = load_engagement(directory)
        _validate_engagement(engagement_value)
        ledger = load_executions(directory)
        _validate_ledger(ledger, str(engagement_value["id"]))
        record = next(
            (item for item in ledger["executions"] if item.get("id") == execution_id),
            None,
        )
        if record is None:
            raise WorkflowError(f"unknown Agent Control execution: {execution_id}")
        if record["status"] in {"completed", "failed", "uncertain"}:
            return {"engagement_id": engagement_value["id"], "execution": record}
        if record["status"] not in {"queued", "running"}:
            raise WorkflowError("Agent Control execution cannot be polled yet")
        remote_task_id = record.get("remote_task_id")
        if not isinstance(remote_task_id, str) or not remote_task_id:
            raise IntegrityError("Agent Control execution remote task ID is missing")
        _manifest, project_value = _project_for_engagement(directory, engagement_value)
        connector_config = _connector_config(
            project_value,
            str(engagement_value["connector_id"]),
            str(engagement_value["operation"]),
        )

    connector = _connector_factory(connector_config)
    snapshot = connector.poll(remote_task_id)
    with engagement_lock(directory):
        ledger = load_executions(directory)
        record = next(item for item in ledger["executions"] if item.get("id") == execution_id)
        allowed = {
            "queued": {"queued", "running", "completed", "failed"},
            "running": {"running", "completed", "failed"},
        }
        if snapshot.status not in allowed.get(str(record.get("status")), set()):
            raise IntegrityError("Nahonza task status moved backwards")
        record["status"] = snapshot.status
        record["phase"] = snapshot.phase
        record["error"] = snapshot.error
        if snapshot.status == "completed":
            receipt = {
                "type": "agent-control-result-receipt",
                "schema_version": SCHEMA_VERSION,
                "engagement_id": engagement_value["id"],
                "execution_id": execution_id,
                "connector_id": engagement_value["connector_id"],
                "remote_task_id": remote_task_id,
                "request_digest": record["request_digest"],
                "result": snapshot.result,
                "received_at": _now(),
            }
            receipt["result_digest"] = canonical_digest(snapshot.result)
            write_json(directory, Path("results") / f"{execution_id}.json", receipt)
            record["result_digest"] = receipt["result_digest"]
        record["updated_at"] = _now()
        _validate_ledger(ledger, str(engagement_value["id"]))
        write_json(directory, EXECUTIONS_FILE, ledger)
    return {"engagement_id": engagement_value["id"], "execution": record}


def engagement_status(engagement: str | Path) -> dict[str, Any]:
    directory = resolve_engagement_directory(engagement)
    with engagement_lock(directory):
        value = load_engagement(directory)
        _validate_engagement(value)
        ledger = load_executions(directory)
        _validate_ledger(ledger, str(value["id"]))
        approval = _approval(
            directory,
            value,
            allow_missing=value["status"] == "proposed",
        )
    return {
        "engagement": value,
        "approval": approval,
        "executions": ledger["executions"],
        "path": str(directory),
    }
