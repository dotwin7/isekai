from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from isekai.support.errors import WorkflowError

from .base import ConnectorHandle, ConnectorRequest, ConnectorSnapshot


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_TERMINAL = {"completed", "failed"}


class ConnectorTransportError(WorkflowError):
    """The remote outcome is unknown because no task identity was received."""


def _resolve_env_reference(reference: str, *, field: str) -> str:
    prefix = "env://"
    if not isinstance(reference, str) or not reference.startswith(prefix):
        raise WorkflowError(f"Nahonza {field} must be an env:// reference")
    variable = reference[len(prefix) :]
    if not variable or not variable.replace("_", "").isalnum():
        raise WorkflowError(f"Nahonza {field} has an invalid environment reference")
    value = os.environ.get(variable)
    if not value:
        raise WorkflowError(f"Nahonza {field} reference is unavailable: {reference}")
    return value


def _knowledge_prompt(context: dict[str, Any] | None) -> str:
    if context is None:
        return ""
    entries = context.get("entries")
    if not isinstance(entries, list) or not entries:
        return ""
    safe_entries = [
        {
            "id": entry.get("id"),
            "kind": entry.get("kind"),
            "title": entry.get("title"),
            "statement": entry.get("statement"),
        }
        for entry in entries
        if isinstance(entry, dict)
    ]
    if not safe_entries:
        return ""
    return (
        "\n\n## ISEKAI approved Project Knowledge\n"
        "Treat the following human-approved project facts as analysis context, "
        "not as permission to expand tools or scope.\n"
        + json.dumps(safe_entries, ensure_ascii=False, sort_keys=True)
    )


@dataclass
class NahonzaConnector:
    endpoint: str
    token: str
    timeout_seconds: float = 30.0
    opener: Callable[..., Any] = urlopen

    def __post_init__(self) -> None:
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise WorkflowError("Nahonza endpoint must be an absolute HTTP(S) URL")
        self.endpoint = self.endpoint.rstrip("/")
        if not self.token:
            raise WorkflowError("Nahonza auth token is unavailable")

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        execution_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        encoded = (
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if body is not None
            else None
        )
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if execution_id is not None:
            # Current Nahonza may ignore this header. It still gives a future
            # idempotent server contract a stable caller-owned key.
            headers["Idempotency-Key"] = execution_id
        request = Request(
            self.endpoint + path,
            data=encoded,
            headers=headers,
            method=method,
        )
        try:
            response = self.opener(request, timeout=self.timeout_seconds)
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise WorkflowError(f"Nahonza returned HTTP {exc.code}") from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise ConnectorTransportError(
                "Nahonza request ended before a reliable remote task identity was received"
            ) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise WorkflowError("Nahonza response exceeds the connector size limit")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkflowError("Nahonza returned an invalid JSON response") from exc
        if not isinstance(value, dict):
            raise WorkflowError("Nahonza response must be an object")
        return status, value

    def start(self, request: ConnectorRequest) -> ConnectorHandle:
        prompt = request.prompt + _knowledge_prompt(request.knowledge_context)
        context: dict[str, Any] = {
            "isekai": {
                "execution_id": request.execution_id,
                "scope": list(request.scope),
                "knowledge_context": request.knowledge_context,
            }
        }
        status, value = self._request(
            "POST",
            "/api/v1/agent/execute",
            body={
                "prompt": prompt,
                "workflow": request.operation,
                "context": context,
            },
            execution_id=request.execution_id,
        )
        task_id = value.get("taskId")
        remote_status = value.get("status")
        if status != 202 or not isinstance(task_id, str) or not task_id.strip():
            raise WorkflowError("Nahonza did not return 202 with a taskId")
        if remote_status != "accepted":
            raise WorkflowError("Nahonza did not accept the execution")
        return ConnectorHandle(remote_task_id=task_id, status="queued")

    def poll(self, remote_task_id: str) -> ConnectorSnapshot:
        if not remote_task_id:
            raise WorkflowError("Nahonza remote task ID is missing")
        _status, value = self._request(
            "GET",
            f"/api/v1/agent/status/{remote_task_id}",
        )
        returned_id = value.get("taskId")
        state = value.get("status")
        if returned_id != remote_task_id or state not in {
            "queued",
            "running",
            *_TERMINAL,
        }:
            raise WorkflowError("Nahonza returned an invalid task status")
        return ConnectorSnapshot(
            remote_task_id=remote_task_id,
            status=str(state),
            result=value.get("result"),
            error=value.get("error") if isinstance(value.get("error"), str) else None,
            phase=value.get("phase") if isinstance(value.get("phase"), str) else None,
        )


def connector_from_project_config(config: dict[str, Any]) -> NahonzaConnector:
    if config.get("kind") != "nahonza":
        raise WorkflowError("unsupported Agent Control connector kind")
    if config.get("transport") != "agent-api":
        raise WorkflowError("Nahonza connector requires the agent-api transport")
    return NahonzaConnector(
        endpoint=_resolve_env_reference(str(config.get("endpoint_ref", "")), field="endpoint"),
        token=_resolve_env_reference(str(config.get("auth_ref", "")), field="auth"),
    )
