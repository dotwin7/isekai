from __future__ import annotations

import copy
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from isekai.support.jsonio import ensure_directory_beneath, unlink_file_beneath
from isekai.workflow.errors import IntegrityError, WorkflowError
from isekai.workflow.project import load_project
from isekai.workflow.project_knowledge import current_project_knowledge

from .connectors import Connector, ConnectorRequest, connector_from_project_config
from .connectors.nahonza import ConnectorTransportError
from .integrity import (
    approved_contract,
    seal_execution,
    validate_approval,
    validate_engagement,
    validate_ledger,
    validate_result_receipts,
)
from .schema import (
    IN_FLIGHT_STATUSES,
    SCHEMA_VERSION,
    canonical_digest,
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
_CONNECTOR_FIELDS = {
    "id",
    "kind",
    "transport",
    "endpoint_ref",
    "auth_ref",
    "allowed_operations",
    "maximum_action_level",
}


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
            set(connector) != _CONNECTOR_FIELDS
            or connector.get("kind") != "nahonza"
            or connector.get("transport") != "agent-api"
            or not isinstance(operations, list)
            or not operations
            or any(not isinstance(item, str) or not item for item in operations)
            or len(operations) != len(set(operations))
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


def _connector_contract(connector: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": connector["id"],
        "kind": connector["kind"],
        "transport": connector["transport"],
        "endpoint_ref": connector["endpoint_ref"],
        "auth_ref": connector["auth_ref"],
        "allowed_operations": sorted(connector["allowed_operations"]),
        "maximum_action_level": connector["maximum_action_level"],
    }


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
        "connector_contract": _connector_contract(connector),
        "scope": list(scope),
        "maximum_executions": maximum_executions,
        "status": "proposed",
        "knowledge_context": knowledge,
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }
    validate_engagement(value)
    ledger = {
        "type": "agent-control-execution-ledger",
        "schema_version": SCHEMA_VERSION,
        "engagement_id": engagement_id,
        "executions": [],
    }
    validate_ledger(ledger, engagement_id)
    engagements_root = ensure_directory_beneath(
        manifest.parent,
        ENGAGEMENTS_DIRECTORY,
        mode=0o700,
    )
    directory = engagements_root / engagement_id
    if directory.exists() or directory.is_symlink():
        raise IntegrityError("Agent Control engagement id collision")
    staging = tempfile.TemporaryDirectory(
        prefix=f".{engagement_id}.stage-",
        dir=engagements_root,
    )
    staged_directory = Path(staging.name)
    renamed = False
    try:
        write_json(staged_directory, ENGAGEMENT_FILE, value)
        write_json(staged_directory, EXECUTIONS_FILE, ledger)
        persisted_engagement = read_json(
            staged_directory / ENGAGEMENT_FILE,
            root=staged_directory,
            label="Agent Control staged engagement",
        )
        persisted_ledger = read_json(
            staged_directory / EXECUTIONS_FILE,
            root=staged_directory,
            label="Agent Control staged execution ledger",
        )
        validate_engagement(persisted_engagement)
        validate_ledger(persisted_ledger, engagement_id)
        staged_directory.rename(directory)
        renamed = True
        validate_engagement(load_engagement(directory))
        validate_ledger(load_executions(directory), engagement_id)
    except Exception as exc:
        if renamed:
            try:
                directory.rename(staged_directory)
            except Exception as restore_exc:
                raise IntegrityError(
                    "Agent Control engagement creation failed and rollback failed: "
                    + str(restore_exc)
                ) from exc
        raise
    finally:
        staging.cleanup()
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
        validate_engagement(value)
        recovering = value["status"] == "active"
        if value["status"] not in {"proposed", "active"}:
            raise WorkflowError("only a proposed engagement can be approved")
        approval_path = directory / APPROVAL_FILE
        if not recovering and (approval_path.exists() or approval_path.is_symlink()):
            raise IntegrityError(
                "Agent Control proposed engagement has an unexpected approval"
            )
        if recovering:
            try:
                existing = read_json(
                    directory / APPROVAL_FILE,
                    root=directory,
                    label="Agent Control engagement approval",
                )
            except FileNotFoundError:
                existing = None
            if existing is not None:
                validate_approval(existing, value)
                raise WorkflowError("only a proposed engagement can be approved")
        approved_contract_value = approved_contract(value)
        approval = {
            "type": "agent-control-engagement-approval",
            "schema_version": SCHEMA_VERSION,
            "engagement_id": value["id"],
            "outcome": "approved",
            "summary": summary,
            "decided_by": decided_by,
            "approved_at": _now(),
            "contract_digest": canonical_digest(approved_contract_value),
        }
        approval["approval_digest"] = canonical_digest(approval)
        validate_approval(approval, value)
        original = copy.deepcopy(value)
        value["status"] = "active"
        value["updated_at"] = approval["approved_at"]
        validate_engagement(value)
        try:
            write_json(directory, ENGAGEMENT_FILE, value)
            write_json(directory, APPROVAL_FILE, approval)
            persisted = load_engagement(directory)
            validate_engagement(persisted)
            persisted_approval = _approval(directory, persisted)
            if persisted_approval is None:  # pragma: no cover - allow_missing is false
                raise IntegrityError("Agent Control approval postflight failed")
        except Exception as exc:
            rollback_errors: list[str] = []
            try:
                # Rollback must not depend on the operation-specific writer seam
                # that failed while committing the approval.
                from isekai.support.jsonio import write_json_atomic_beneath

                write_json_atomic_beneath(directory, ENGAGEMENT_FILE, original)
            except Exception as restore_exc:
                rollback_errors.append(str(restore_exc))
            try:
                unlink_file_beneath(directory, APPROVAL_FILE, missing_ok=True)
            except Exception as restore_exc:
                rollback_errors.append(str(restore_exc))
            if rollback_errors:
                raise IntegrityError(
                    "Agent Control approval failed and rollback failed: "
                    + "; ".join(rollback_errors)
                ) from exc
            raise
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
    validate_approval(approval, engagement)
    return approval


def _project_for_engagement(directory: Path, engagement: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    project_root = directory.parent.parent
    manifest, project = _project(project_root / "project.json")
    if project.get("id") != engagement.get("project_id"):
        raise IntegrityError("Agent Control engagement does not match its Project")
    return manifest, project


def _current_connector_for_engagement(
    project: dict[str, Any], engagement: dict[str, Any]
) -> dict[str, Any]:
    connector = _connector_config(
        project,
        str(engagement["connector_id"]),
        str(engagement["operation"]),
    )
    if _connector_contract(connector) != engagement.get("connector_contract"):
        raise IntegrityError("Agent Control approved connector contract has changed")
    project_level = str(project.get("maximum_agent_level", "L0"))
    action_level = str(engagement.get("action_level"))
    if _ACTION_LEVELS.get(action_level, 99) > _ACTION_LEVELS.get(project_level, -1):
        raise IntegrityError(
            f"Agent Control action level {action_level} exceeds current Project {project_level}"
        )
    return connector


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
    if not isinstance(prompt, str) or not prompt.strip():
        raise WorkflowError("execution prompt must be a non-empty string")
    if not isinstance(requested_by, str) or not requested_by.strip():
        raise WorkflowError("execution requested_by must be a non-empty string")
    with engagement_lock(directory):
        engagement_value = load_engagement(directory)
        validate_engagement(engagement_value)
        if engagement_value["status"] != "active":
            raise WorkflowError("Agent Control engagement must be active")
        approval = _approval(directory, engagement_value)
        if approval is None:  # pragma: no cover - allow_missing is false
            raise IntegrityError("Agent Control engagement approval is missing")
        ledger = load_executions(directory)
        validate_ledger(ledger, str(engagement_value["id"]))
        validate_result_receipts(directory, engagement_value, ledger)
        executions = ledger["executions"]
        if any(item.get("status") in IN_FLIGHT_STATUSES for item in executions):
            raise WorkflowError("Agent Control engagement already has an in-flight execution")
        if len(executions) >= engagement_value["maximum_executions"]:
            raise WorkflowError("Agent Control engagement execution budget is exhausted")
        if (
            not scope
            or any(not isinstance(item, str) or not item.strip() for item in scope)
            or len(scope) != len(set(scope))
            or not set(scope).issubset(set(engagement_value["scope"]))
        ):
            raise WorkflowError("execution scope must be a non-empty engagement scope subset")
        prior = list(prior_execution_ids or [])
        known_ids = {str(item.get("id")) for item in executions}
        if len(prior) != len(set(prior)) or not set(prior).issubset(known_ids):
            raise WorkflowError("prior_execution_ids must reference this engagement")
        _manifest, project_value = _project_for_engagement(directory, engagement_value)
        connector_config = _current_connector_for_engagement(
            project_value, engagement_value
        )
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
            "result_receipt_digest": None,
            "error": None,
            "created_at": _now(),
            "updated_at": _now(),
            "previous_execution_digest": (
                executions[-1].get("execution_digest") if executions else None
            ),
        }
        seal_execution(execution)
        executions.append(execution)
        validate_ledger(ledger, str(engagement_value["id"]))
        write_json(directory, EXECUTIONS_FILE, ledger)

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
        if (
            handle.status != "queued"
            or not isinstance(handle.remote_task_id, str)
            or not handle.remote_task_id.strip()
        ):
            terminal_status = "failed"
            remote_task_id = None
            error = "connector returned an invalid task handle"
        else:
            terminal_status = handle.status
            remote_task_id = handle.remote_task_id
            error = None

    with engagement_lock(directory):
        ledger = load_executions(directory)
        validate_ledger(ledger, str(engagement_value["id"]))
        validate_result_receipts(directory, engagement_value, ledger)
        record = next(
            (item for item in ledger["executions"] if item.get("id") == execution_id),
            None,
        )
        if record is None:
            raise IntegrityError("Agent Control execution disappeared during dispatch")
        if record.get("status") != "dispatching":
            raise IntegrityError("Agent Control execution changed during connector dispatch")
        record["status"] = terminal_status
        record["remote_task_id"] = remote_task_id
        record["error"] = error
        record["updated_at"] = _now()
        seal_execution(record)
        validate_ledger(ledger, str(engagement_value["id"]))
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
        validate_engagement(engagement_value)
        ledger = load_executions(directory)
        validate_ledger(ledger, str(engagement_value["id"]))
        validate_result_receipts(directory, engagement_value, ledger)
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
        connector_config = _current_connector_for_engagement(
            project_value, engagement_value
        )

    connector = _connector_factory(connector_config)
    snapshot = connector.poll(remote_task_id)
    if snapshot.remote_task_id != remote_task_id:
        raise IntegrityError("Agent Control connector returned a different task ID")
    with engagement_lock(directory):
        ledger = load_executions(directory)
        validate_ledger(ledger, str(engagement_value["id"]))
        validate_result_receipts(directory, engagement_value, ledger)
        original_ledger = copy.deepcopy(ledger)
        record = next(
            (item for item in ledger["executions"] if item.get("id") == execution_id),
            None,
        )
        if record is None:
            raise IntegrityError("Agent Control execution disappeared during polling")
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
            receipt["receipt_digest"] = canonical_digest(receipt)
            record["result_digest"] = receipt["result_digest"]
            record["result_receipt_digest"] = receipt["receipt_digest"]
        record["updated_at"] = _now()
        seal_execution(record)
        validate_ledger(ledger, str(engagement_value["id"]))
        receipt_relative = Path("results") / f"{execution_id}.json"
        receipt_path = directory / receipt_relative
        if snapshot.status == "completed" and (
            receipt_path.exists() or receipt_path.is_symlink()
        ):
            raise IntegrityError(
                f"Agent Control result receipt already exists: {execution_id}"
            )
        receipt_written = False
        ledger_attempted = False
        try:
            if snapshot.status == "completed":
                write_json(directory, receipt_relative, receipt)
                receipt_written = True
            ledger_attempted = True
            write_json(directory, EXECUTIONS_FILE, ledger)
            validate_result_receipts(directory, engagement_value, ledger)
        except Exception as exc:
            rollback_errors: list[str] = []
            if ledger_attempted:
                try:
                    from isekai.support.jsonio import write_json_atomic_beneath

                    write_json_atomic_beneath(
                        directory,
                        EXECUTIONS_FILE,
                        original_ledger,
                    )
                except Exception as restore_exc:
                    rollback_errors.append(str(restore_exc))
            if receipt_written:
                try:
                    unlink_file_beneath(directory, receipt_relative, missing_ok=True)
                except Exception as restore_exc:
                    rollback_errors.append(str(restore_exc))
            if rollback_errors:
                raise IntegrityError(
                    "Agent Control result persistence failed and rollback failed: "
                    + "; ".join(rollback_errors)
                ) from exc
            raise
    return {"engagement_id": engagement_value["id"], "execution": record}


def engagement_status(engagement: str | Path) -> dict[str, Any]:
    directory = resolve_engagement_directory(engagement)
    with engagement_lock(directory):
        value = load_engagement(directory)
        validate_engagement(value)
        ledger = load_executions(directory)
        validate_ledger(ledger, str(value["id"]))
        validate_result_receipts(directory, value, ledger)
        approval = _approval(
            directory,
            value,
            allow_missing=value["status"] == "proposed",
        )
        if value["status"] == "proposed" and approval is not None:
            raise IntegrityError(
                "Agent Control proposed engagement has an unexpected approval"
            )
    return {
        "engagement": value,
        "approval": approval,
        "executions": ledger["executions"],
        "path": str(directory),
    }
