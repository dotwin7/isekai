from __future__ import annotations

from typing import Any, Mapping

from . import __version__
from .distribution import PROTOCOL_VERSION
from .plugin.actions import PluginError, execute_action, load_compatibility


PLUGIN_ID = "isekai-agent-plugin"
PLUGIN_VERSION = __version__


def _envelope(action: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "plugin": PLUGIN_ID,
        "plugin_version": PLUGIN_VERSION,
        "core_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "action": action,
        "result": result,
    }


def dispatch(
    action: str,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one host-neutral plugin action and wrap its stable response."""
    return _envelope(action, execute_action(action, payload))


__all__ = [
    "PLUGIN_ID",
    "PLUGIN_VERSION",
    "PluginError",
    "dispatch",
    "load_compatibility",
]
