from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .distribution import PROTOCOL_VERSION, verify_adapter_handshake
from .intake import intake
from .foundation import (
    FoundationError,
    load_foundation,
    promote_foundation,
    record_foundation_decision,
    record_foundation_evidence,
)
from .session import (
    build_session,
    inception_session,
    resume_session,
    update_checkpoint,
)
from .workflow import (
    RouteRequest,
    WorkRoute,
    classify_work,
    initialize_unit,
    authorize_action,
    propose_execution_envelope,
    record_decision,
    record_evidence,
    transition_unit,
    verify_unit,
)


PLUGIN_ID = "isekai-agent-plugin"
PLUGIN_VERSION = __version__


class PluginError(ValueError):
    """Raised for invalid or unsafe plugin requests."""



COMPATIBILITY_PATH = Path(__file__).resolve().parent / "data/compatibility.json"


def load_compatibility() -> dict[str, Any]:
    try:
        value = json.loads(COMPATIBILITY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PluginError(f"missing compatibility matrix: {COMPATIBILITY_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise PluginError(f"invalid compatibility matrix: {exc}") from exc
    if not isinstance(value, dict):
        raise PluginError("compatibility matrix must be an object")
    return value

def _required(payload: Mapping[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is None or value == "":
        raise PluginError(f"missing plugin request field: {key}")
    return value


def _envelope(action: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "plugin": PLUGIN_ID,
        "plugin_version": PLUGIN_VERSION,
        "core_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "action": action,
        "result": result,
    }


def dispatch(action: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    values = dict(payload or {})
    if action == "handshake":
        try:
            result = verify_adapter_handshake(
                str(_required(values, "runtime")),
                str(_required(values, "adapter_version")),
                str(_required(values, "protocol_version")),
                values.get("project", "."),
            )
        except ValueError as exc:
            raise PluginError(str(exc)) from exc
        return _envelope(
            action,
            result,
        )
    if action == "init":
        from .workflow import initialize_project

        path = initialize_project(
            values.get("path", "."),
            project_id=values.get("project_id"),
            foundation_path=(
                str(values["foundation_path"])
                if values.get("foundation_path") is not None
                else None
            ),
            profiles=list(values.get("profiles", [])),
            document_language=str(values.get("document_language", "ko")),
            maximum_agent_level=str(values.get("maximum_agent_level", "L0")),
        )
        return _envelope(
            action,
            {"created": str(path), "units": str(path.parent / "units")},
        )
    if action == "on":
        from .session import activate_session

        if values.get("unit") is not None and values.get("unit") != "":
            raise PluginError("on does not select a Unit; use resume --unit PATH")
        return _envelope(
            action,
            activate_session(values.get("project", ".")),
        )
    if action == "off":
        from .session import deactivate_session

        return _envelope(action, deactivate_session())
    if action == "intake":
        return _envelope(action, intake(values))
    if action == "status":
        return _envelope(
            action,
            build_session(values.get("project", "."), values.get("unit")),
        )
    if action == "resume":
        return _envelope(
            action,
            resume_session(values.get("project", "."), values.get("unit")),
        )
    if action == "inception":
        return _envelope(action, inception_session(values.get("project", ".")))
    if action == "compatibility":
        return _envelope(action, load_compatibility())
    if action == "release-check":
        return _envelope(
            action,
            load_foundation(values.get("foundation", "foundation")).readiness(),
        )
    if action == "foundation-decision":
        return _envelope(
            action,
            record_foundation_decision(
                values.get("foundation", "foundation"),
                outcome=str(_required(values, "outcome")),
                summary=str(_required(values, "summary")),
                decided_by=str(_required(values, "decided_by")),
            ),
        )
    if action == "foundation-evidence":
        return _envelope(
            action,
            record_foundation_evidence(
                values.get("foundation", "foundation"),
                passed=values.get("passed") is True,
                checks=list(values.get("checks", [])),
                scope=str(_required(values, "scope")),
                recorded_by=str(_required(values, "recorded_by")),
            ),
        )
    if action == "foundation-promote":
        return _envelope(
            action,
            promote_foundation(values.get("foundation", "foundation")),
        )
    if action == "route":
        request = RouteRequest(
            change=str(_required(values, "change")),
            risk=str(values.get("risk", "low")),
            ambiguous=bool(values.get("ambiguous", False)),
            multi_party=bool(values.get("multi_party", False)),
            remote=bool(values.get("remote", False)),
            sensitive=bool(values.get("sensitive", False)),
        )
        return _envelope(action, classify_work(request).as_dict())
    if action == "unit-init":
        path = initialize_unit(
            _required(values, "project"),
            str(_required(values, "title")),
            values.get("output"),
            str(values.get("owner", "unassigned")),
            intent=values.get("intent"),
        )
        return _envelope(action, {"created": str(path)})
    if action == "checkpoint":
        return _envelope(
            action,
            update_checkpoint(
                _required(values, "unit"),
                completed=list(values.get("completed", [])),
                pending=list(values.get("pending", [])),
                blocked_by=list(values.get("blocked_by", [])),
                next_action=str(_required(values, "next_action")),
            ),
        )
    if action == "envelope-propose":
        return _envelope(
            action,
            propose_execution_envelope(
                _required(values, "unit"),
                scope=list(values.get("scope", [])),
                stages=list(values.get("stages", [])),
                allowed_actions=list(values.get("allowed_actions", [])),
                forbidden_actions=list(values.get("forbidden_actions", [])),
                max_iterations=int(values.get("max_iterations", 0)),
                proposed_by=str(_required(values, "proposed_by")),
            ),
        )
    if action == "authorize":
        return _envelope(
            action,
            authorize_action(
                _required(values, "unit"),
                action=str(_required(values, "requested_action")),
                target=values.get("target"),
                stage=values.get("stage"),
            ),
        )
    if action == "evidence":
        return _envelope(
            action,
            record_evidence(
                _required(values, "unit"),
                passed=values.get("passed") is True,
                commands=list(values.get("commands", [])),
                scope=str(_required(values, "scope")),
                recorded_by=str(_required(values, "recorded_by")),
                notes=str(values.get("notes", "")),
            ),
        )
    if action == "decision":
        return _envelope(
            action,
            record_decision(
                _required(values, "unit"),
                gate=str(_required(values, "gate")),
                outcome=str(_required(values, "outcome")),
                summary=str(_required(values, "summary")),
                rationale=list(_required(values, "rationale")),
                alternatives=list(values.get("alternatives", [])),
                tradeoffs=list(values.get("tradeoffs", [])),
                risks=list(values.get("risks", [])),
                references=list(values.get("references", [])),
                decided_by=str(_required(values, "decided_by")),
            ),
        )
    if action == "transition":
        return _envelope(
            action,
            transition_unit(
                _required(values, "unit"),
                str(_required(values, "to")),
            ),
        )
    if action == "verify":
        return _envelope(action, verify_unit(_required(values, "unit")))
    raise PluginError(f"unsupported plugin action: {action}")
