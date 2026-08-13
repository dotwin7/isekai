from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import SplitResult, quote, urlsplit, urlunsplit
from urllib.request import Request

from isekai.support.errors import WorkflowError

from .base import ConnectorHandle, ConnectorRequest, ConnectorSnapshot


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_TERMINAL = {"completed", "failed"}
_EXTERNAL_HOST = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to one validated address while retaining TLS hostname checks."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        pinned_address: str,
        timeout: float,
    ) -> None:
        self._pinned_address = pinned_address
        self._tls_context = ssl.create_default_context()
        super().__init__(
            host,
            port=port,
            timeout=timeout,
            context=self._tls_context,
        )

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._pinned_address, self.port),
            timeout=self.timeout,
        )
        try:
            self.sock = self._tls_context.wrap_socket(
                raw_socket,
                server_hostname=self.host,
            )
        except Exception:
            raw_socket.close()
            raise


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
    opener: Callable[..., Any] | None = None
    resolver: Callable[..., Any] = socket.getaddrinfo
    _parsed_endpoint: SplitResult = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            parsed = urlsplit(self.endpoint)
            port = parsed.port
        except ValueError as exc:
            raise WorkflowError("Nahonza endpoint is invalid") from exc
        host = parsed.hostname.lower() if parsed.hostname else ""
        if (
            parsed.scheme.lower() != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.query
            or parsed.fragment
            or _EXTERNAL_HOST.fullmatch(host) is None
        ):
            raise WorkflowError(
                "Nahonza endpoint must be an external HTTPS DNS URL on port 443"
            )
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:  # pragma: no cover - the DNS pattern already excludes literals
            raise WorkflowError("Nahonza endpoint cannot use an IP literal")
        path = parsed.path.rstrip("/")
        if (
            path.startswith("//")
            or "\\" in path
            or any(character.isspace() for character in path)
            or ".." in path.split("/")
        ):
            raise WorkflowError("Nahonza endpoint contains an unsafe path")
        self.endpoint = urlunsplit(("https", host, path, "", ""))
        self._parsed_endpoint = urlsplit(self.endpoint)
        if not self.token:
            raise WorkflowError("Nahonza auth token is unavailable")

    def _resolved_global_address(self) -> str:
        host = str(self._parsed_endpoint.hostname)
        try:
            answers = self.resolver(
                host,
                443,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise ConnectorTransportError(
                "Nahonza endpoint DNS resolution failed before dispatch"
            ) from exc
        try:
            addresses = sorted(
                {str(answer[4][0]) for answer in answers if len(answer) > 4 and answer[4]}
            )
            parsed_addresses = [ipaddress.ip_address(address) for address in addresses]
        except (IndexError, TypeError, ValueError) as exc:
            raise ConnectorTransportError(
                "Nahonza endpoint DNS resolution returned invalid addresses"
            ) from exc
        if not addresses:
            raise ConnectorTransportError(
                "Nahonza endpoint DNS resolution returned no addresses"
            )
        if any(not address.is_global for address in parsed_addresses):
            raise WorkflowError(
                "Nahonza endpoint DNS must resolve only to global addresses"
            )
        return addresses[0]

    def _direct_request(
        self,
        method: str,
        path: str,
        *,
        encoded: bytes | None,
        headers: dict[str, str],
    ) -> tuple[int, bytes]:
        host = str(self._parsed_endpoint.hostname)
        connection = _PinnedHTTPSConnection(
            host,
            443,
            pinned_address=self._resolved_global_address(),
            timeout=self.timeout_seconds,
        )
        target = (self._parsed_endpoint.path.rstrip("/") + path) or "/"
        try:
            connection.request(method, target, body=encoded, headers=headers)
            response = connection.getresponse()
            status = int(response.status)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            raise ConnectorTransportError(
                "Nahonza request ended before a reliable remote task identity was received"
            ) from exc
        finally:
            connection.close()
        return status, raw

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
        if self.opener is None:
            status, raw = self._direct_request(
                method,
                path,
                encoded=encoded,
                headers=headers,
            )
        else:
            request = Request(
                self.endpoint + path,
                data=encoded,
                headers=headers,
                method=method,
            )
            response: Any | None = None
            try:
                response = self.opener(request, timeout=self.timeout_seconds)
                response_status = getattr(response, "status", None)
                status = int(
                    response_status if response_status is not None else response.getcode()
                )
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            except HTTPError as exc:
                raise WorkflowError(f"Nahonza returned HTTP {exc.code}") from exc
            except (TimeoutError, URLError, OSError) as exc:
                raise ConnectorTransportError(
                    "Nahonza request ended before a reliable remote task identity was received"
                ) from exc
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        if status < 200 or status >= 300:
            raise WorkflowError(f"Nahonza returned HTTP {status}")
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
            f"/api/v1/agent/status/{quote(remote_task_id, safe='')}",
        )
        if _status != 200:
            raise WorkflowError("Nahonza status endpoint did not return HTTP 200")
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
