from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

from ..support.files import UnsafeControlFile, read_control_file
from ..distribution import verify_adapter_handshake
from ..foundation import (
    load_foundation,
    promote_foundation,
    record_foundation_decision,
    record_foundation_evidence,
)
from ..workflow.intake import intake
from ..workflow.session import (
    activate_session,
    build_session,
    deactivate_session,
    inception_session,
    resume_session,
    update_checkpoint,
)
from ..workflow import (
    EXECUTION_ENVELOPE_DEFAULT_HOURS,
    RouteRequest,
    approve_execution_envelope,
    authorize_action,
    classify_work,
    initialize_project,
    initialize_unit,
    propose_execution_envelope,
    record_decision,
    record_evidence,
    transition_unit,
    verify_unit,
)


class PluginError(ValueError):
    """Raised for invalid or unsafe plugin requests."""


COMPATIBILITY_PATH = Path(__file__).resolve().parents[1] / "data/compatibility.json"


def load_compatibility() -> dict[str, Any]:
    try:
        content = read_control_file(
            COMPATIBILITY_PATH,
            root=COMPATIBILITY_PATH.parent,
            label="compatibility matrix",
        ).decode("utf-8")
        value = json.loads(content)
    except FileNotFoundError as exc:
        raise PluginError(f"missing compatibility matrix: {COMPATIBILITY_PATH}") from exc
    except UnsafeControlFile as exc:
        raise PluginError(str(exc)) from exc
    except OSError as exc:
        raise PluginError(f"cannot safely read compatibility matrix: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PluginError(f"invalid compatibility matrix: {exc}") from exc
    if not isinstance(value, dict):
        raise PluginError("compatibility matrix must be an object")
    return value


def _required(payload: Mapping[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is None or value == "":
        raise PluginError(f"missing plugin request field: {key}")
    return value


def _list_field(payload: Mapping[str, Any], key: str) -> list[Any]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise PluginError(f"plugin request field {key} must be a list")
    return list(value)


def _handshake(values: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return verify_adapter_handshake(
            str(_required(values, "runtime")),
            str(_required(values, "adapter_version")),
            str(_required(values, "protocol_version")),
            values.get("project", "."),
        )
    except ValueError as exc:
        raise PluginError(str(exc)) from exc


def _init(values: Mapping[str, Any]) -> dict[str, Any]:
    path = initialize_project(
        values.get("path", "."),
        project_id=values.get("project_id"),
        foundation_path=(
            str(values["foundation_path"])
            if values.get("foundation_path") is not None
            else None
        ),
        profiles=_list_field(values, "profiles"),
        document_language=str(values.get("document_language", "ko")),
        maximum_agent_level=str(values.get("maximum_agent_level", "L0")),
    )
    return {"created": str(path), "units": str(path.parent / "units")}


def _on(values: Mapping[str, Any]) -> dict[str, Any]:
    if values.get("unit") is not None and values.get("unit") != "":
        raise PluginError("on does not select a Unit; use resume --unit PATH")
    return activate_session(values.get("project", "."))


def _off(_values: Mapping[str, Any]) -> dict[str, Any]:
    return deactivate_session()


def _status(values: Mapping[str, Any]) -> dict[str, Any]:
    return build_session(values.get("project", "."), values.get("unit"))


def _resume(values: Mapping[str, Any]) -> dict[str, Any]:
    return resume_session(values.get("project", "."), values.get("unit"))


def _inception(values: Mapping[str, Any]) -> dict[str, Any]:
    return inception_session(values.get("project", "."))


def _release_check(values: Mapping[str, Any]) -> dict[str, Any]:
    return load_foundation(values.get("foundation", "foundation")).readiness()


def _foundation_decision(values: Mapping[str, Any]) -> dict[str, Any]:
    return record_foundation_decision(
        values.get("foundation", "foundation"),
        outcome=str(_required(values, "outcome")),
        summary=str(_required(values, "summary")),
        decided_by=str(_required(values, "decided_by")),
    )


def _foundation_evidence(values: Mapping[str, Any]) -> dict[str, Any]:
    return record_foundation_evidence(
        values.get("foundation", "foundation"),
        passed=values.get("passed") is True,
        checks=_list_field(values, "checks"),
        scope=str(_required(values, "scope")),
        recorded_by=str(_required(values, "recorded_by")),
    )


def _foundation_promote(values: Mapping[str, Any]) -> dict[str, Any]:
    return promote_foundation(values.get("foundation", "foundation"))


def _route(values: Mapping[str, Any]) -> dict[str, Any]:
    request = RouteRequest(
        change=str(_required(values, "change")),
        risk=str(values.get("risk", "low")),
        ambiguous=bool(values.get("ambiguous", False)),
        multi_party=bool(values.get("multi_party", False)),
        remote=bool(values.get("remote", False)),
        sensitive=bool(values.get("sensitive", False)),
    )
    return classify_work(request).as_dict()


def _unit_init(values: Mapping[str, Any]) -> dict[str, Any]:
    path = initialize_unit(
        _required(values, "project"),
        str(_required(values, "title")),
        values.get("output"),
        str(values.get("owner", "unassigned")),
        intent=values.get("intent"),
    )
    return {"created": str(path)}


def _checkpoint(values: Mapping[str, Any]) -> dict[str, Any]:
    return update_checkpoint(
        _required(values, "unit"),
        completed=_list_field(values, "completed"),
        pending=_list_field(values, "pending"),
        blocked_by=_list_field(values, "blocked_by"),
        next_action=str(_required(values, "next_action")),
    )


def _envelope_propose(values: Mapping[str, Any]) -> dict[str, Any]:
    return propose_execution_envelope(
        _required(values, "unit"),
        scope=_list_field(values, "scope"),
        stages=_list_field(values, "stages"),
        allowed_actions=_list_field(values, "allowed_actions"),
        forbidden_actions=_list_field(values, "forbidden_actions"),
        max_iterations=values.get("max_iterations", 0),
        proposed_by=str(_required(values, "proposed_by")),
        expires_in_hours=values.get(
            "expires_in_hours", EXECUTION_ENVELOPE_DEFAULT_HOURS
        ),
    )


def _authorize(values: Mapping[str, Any]) -> dict[str, Any]:
    return authorize_action(
        _required(values, "unit"),
        action=str(_required(values, "requested_action")),
        target=values.get("target"),
        stage=values.get("stage"),
    )


def _evidence(values: Mapping[str, Any]) -> dict[str, Any]:
    return record_evidence(
        _required(values, "unit"),
        passed=values.get("passed") is True,
        commands=_list_field(values, "commands"),
        scope=str(_required(values, "scope")),
        recorded_by=str(_required(values, "recorded_by")),
        notes=str(values.get("notes", "")),
    )


def _decision(values: Mapping[str, Any]) -> dict[str, Any]:
    return record_decision(
        _required(values, "unit"),
        gate=str(_required(values, "gate")),
        outcome=str(_required(values, "outcome")),
        summary=str(_required(values, "summary")),
        rationale=_list_field(values, "rationale"),
        alternatives=_list_field(values, "alternatives"),
        tradeoffs=_list_field(values, "tradeoffs"),
        risks=_list_field(values, "risks"),
        references=_list_field(values, "references"),
        decided_by=str(_required(values, "decided_by")),
    )


def _transition(values: Mapping[str, Any]) -> dict[str, Any]:
    return transition_unit(
        _required(values, "unit"),
        str(_required(values, "to")),
    )


def _verify(values: Mapping[str, Any]) -> dict[str, Any]:
    return verify_unit(_required(values, "unit"))


ActionHandler = Callable[[Mapping[str, Any]], dict[str, Any]]
ACTION_HANDLERS: dict[str, ActionHandler] = {
    "handshake": _handshake,
    "init": _init,
    "on": _on,
    "off": _off,
    "intake": intake,
    "status": _status,
    "resume": _resume,
    "inception": _inception,
    "compatibility": lambda _values: load_compatibility(),
    "release-check": _release_check,
    "foundation-decision": _foundation_decision,
    "foundation-evidence": _foundation_evidence,
    "foundation-promote": _foundation_promote,
    "route": _route,
    "unit-init": _unit_init,
    "checkpoint": _checkpoint,
    "envelope-propose": _envelope_propose,
    "envelope-approve": lambda values: approve_execution_envelope(
        _required(values, "unit")
    ),
    "authorize": _authorize,
    "evidence": _evidence,
    "decision": _decision,
    "transition": _transition,
    "verify": _verify,
}


def execute_action(action: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    handler = ACTION_HANDLERS.get(action)
    if handler is None:
        raise PluginError(f"unsupported plugin action: {action}")
    return handler(dict(payload or {}))
