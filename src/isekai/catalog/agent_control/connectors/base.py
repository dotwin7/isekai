from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ConnectorRequest:
    execution_id: str
    operation: str
    prompt: str
    scope: tuple[str, ...]
    knowledge_context: dict[str, Any] | None


@dataclass(frozen=True)
class ConnectorHandle:
    remote_task_id: str
    status: str


@dataclass(frozen=True)
class ConnectorSnapshot:
    remote_task_id: str
    status: str
    result: Any = None
    error: str | None = None
    phase: str | None = None


class Connector(Protocol):
    def start(self, request: ConnectorRequest) -> ConnectorHandle: ...

    def poll(self, remote_task_id: str) -> ConnectorSnapshot: ...
