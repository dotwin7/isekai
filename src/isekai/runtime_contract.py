from __future__ import annotations

from typing import Any, Mapping

from . import __version__
from .distribution.release import PROTOCOL_VERSION
from .runtime.actions import execute_action
from .runtime.compatibility import load_compatibility
from .runtime.request_fields import RuntimeContractError


RUNTIME_ID = "isekai-project-runtime"
RUNTIME_VERSION = __version__


def _envelope(action: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime": RUNTIME_ID,
        "runtime_version": RUNTIME_VERSION,
        "core_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "action": action,
        "result": result,
    }


def dispatch(
    action: str,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one host-neutral runtime action and wrap its stable response."""
    return _envelope(action, execute_action(action, payload))


__all__ = [
    "RUNTIME_ID",
    "RUNTIME_VERSION",
    "RuntimeContractError",
    "dispatch",
    "load_compatibility",
]
