from __future__ import annotations

import json
from datetime import date
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
from isekai.catalog.ai_dlc.intake import intake
from ..workflow.session import (
    activate_session,
    build_session,
    deactivate_session,
    discover_project,
    inception_session,
    migrate_unit_context,
    resume_session,
    update_checkpoint,
)
from ..workflow.active_binding import (
    active_unit_action_guard,
    active_unit_creation_guard,
    active_unit_binding,
    bind_active_unit,
    complete_active_unit,
    detach_active_unit,
    project_manifest_for_unit,
    require_active_unit_match,
)
from ..workflow import initialize_project, load_catalog
from ..workflow.project_knowledge import (
    project_knowledge_status,
    promote_project_knowledge,
    propose_project_knowledge,
)
from isekai.catalog.ai_dlc.routing import RouteRequest, classify_work
from isekai.catalog.ai_dlc.unit.amendments import record_unit_amendment
from isekai.catalog.ai_dlc.unit.decisions import record_decision
from isekai.catalog.ai_dlc.unit.evidence import record_evidence
from isekai.catalog.ai_dlc.unit.execution import (
    EXECUTION_ENVELOPE_DEFAULT_HOURS,
    approve_execution_envelope,
    authorize_action,
    propose_execution_envelope,
)
from isekai.catalog.ai_dlc.unit.initialization import initialize_unit
from isekai.catalog.ai_dlc.unit.lifecycle import transition_unit, verify_unit
from isekai.catalog.ai_dlc.unit.managed_execution import (
    execute_managed_edit,
    execute_managed_test,
    write_unit_artifacts,
)
from .catalog_contract import catalog_model_issues

class RuntimeContractError(ValueError):
    """Raised for invalid or unsafe Runtime Skill requests."""

COMPATIBILITY_PATH = Path(__file__).resolve().parents[1] / "data/compatibility.json"
COMPATIBILITY_OBSERVATION_STATUSES = {
    "live-verified",
    "validation-only",
    "unavailable",
    "unlinked-legacy",
}


def _compatibility_issues(value: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if value.get("schema_version") != "1.0.0":
        issues.append("compatibility matrix has an unsupported schema_version")
    if value.get("protocol_version") != "1.1.0":
        issues.append("compatibility matrix has an unsupported protocol_version")
    runtime_contract = value.get("runtime_contract")
    if not isinstance(runtime_contract, dict):
        issues.append("compatibility runtime_contract must be an object")
    else:
        if runtime_contract.get("high_risk_actions") != []:
            issues.append("compatibility runtime_contract cannot allow high-risk actions")
        if runtime_contract.get("human_decision_actions") != [
            "amend",
            "active-unit-detach",
            "decision",
            "foundation-decision",
            "foundation-promote",
        ]:
            issues.append(
                "compatibility runtime_contract has invalid human_decision_actions"
            )
        if runtime_contract.get("external_agent_actions") != ["external-api"]:
            issues.append(
                "compatibility runtime_contract has invalid external_agent_actions"
            )
        if runtime_contract.get("credential_handling") != (
            "opaque-reference-resolved-by-host"
        ):
            issues.append(
                "compatibility runtime_contract has invalid credential_handling"
            )
    trust_model = value.get("trust_model")
    expected_trust_model = {
        "core_enforcement": "record-consistency-tamper-detection-active-unit-binding-and-managed-execution",
        "action_execution": "core-managed-edit-and-test",
        "managed_test_isolation": "os-enforced-source-and-user-data-read-denial-write-confinement-network-denial-fail-closed",
        "conversation_change_reporting": "runtime-adapter-attested-not-core-observed",
        "human_identity": "caller-attested-not-core-verified",
        "evidence_execution": "core-receipted-for-managed-tests",
        "secret_resolution": "runtime-host-outside-core",
        "external_controls_required": [
            "host direct-write tools disabled in favor of the Core gateway",
            "active-Unit user-turn routing by the Runtime Adapter",
            "authenticated human confirmation channel",
            "CI or host execution provenance",
            "host secret broker and output redaction",
        ],
    }
    if trust_model != expected_trust_model:
        issues.append("compatibility matrix has an invalid trust_model")
    issues.extend(catalog_model_issues(value.get("catalog_model")))
    policy = value.get("policy")
    if not isinstance(policy, dict):
        issues.append("compatibility policy must be an object")
    else:
        for field in ("classification", "tested_versions_are", "legacy_versions_are"):
            if not isinstance(policy.get(field), str) or not policy[field].strip():
                issues.append(f"compatibility policy requires {field}")
    runtimes = value.get("runtimes")
    observations = value.get("observations")
    if not isinstance(runtimes, list) or not runtimes:
        return ["compatibility runtimes must be a non-empty list"]
    if not isinstance(observations, list):
        return ["compatibility observations must be a list"]

    observation_by_id: dict[str, dict[str, Any]] = {}
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            issues.append(f"compatibility observation {index} must be an object")
            continue
        observation_id = observation.get("id")
        if not isinstance(observation_id, str) or not observation_id.strip():
            issues.append(f"compatibility observation {index} requires id")
        elif observation_id in observation_by_id:
            issues.append(f"duplicate compatibility observation: {observation_id}")
        else:
            observation_by_id[observation_id] = observation
        status = observation.get("status")
        if status not in COMPATIBILITY_OBSERVATION_STATUSES:
            issues.append(f"compatibility observation {index} has invalid status")
        for field in ("runtime", "evidence_strength", "source_ref"):
            if not isinstance(observation.get(field), str) or not observation[field].strip():
                issues.append(f"compatibility observation {index} requires {field}")
        version = observation.get("version")
        if version is not None and (
            not isinstance(version, str) or not version.strip()
        ):
            issues.append(f"compatibility observation {index} has invalid version")
        observed_on = observation.get("observed_on")
        if observed_on is not None:
            try:
                date.fromisoformat(observed_on)
            except (TypeError, ValueError):
                issues.append(
                    f"compatibility observation {index} has invalid observed_on"
                )
        if status == "unlinked-legacy" and observed_on is not None:
            issues.append(
                f"compatibility observation {index} legacy evidence cannot claim observed_on"
            )
        if status in {"live-verified", "validation-only", "unavailable"} and (
            observed_on is None
        ):
            issues.append(f"compatibility observation {index} requires observed_on")
        if status in {"live-verified", "validation-only", "unlinked-legacy"} and (
            not isinstance(version, str) or not version.strip()
        ):
            issues.append(
                f"compatibility observation {index} status requires version"
            )
        checks = observation.get("checks")
        if not isinstance(checks, list) or not checks or any(
            not isinstance(check, str) or not check.strip() for check in checks
        ):
            issues.append(
                f"compatibility observation {index} requires non-empty checks"
            )

    seen_runtimes: set[str] = set()
    referenced_observations: list[str] = []
    for index, runtime in enumerate(runtimes):
        if not isinstance(runtime, dict):
            issues.append(f"compatibility runtime {index} must be an object")
            continue
        runtime_id = runtime.get("id")
        if not isinstance(runtime_id, str) or not runtime_id.strip():
            issues.append(f"compatibility runtime {index} requires id")
            continue
        if runtime_id in seen_runtimes:
            issues.append(f"duplicate compatibility runtime: {runtime_id}")
        seen_runtimes.add(runtime_id)
        references = runtime.get("evidence_refs")
        if not isinstance(references, list) or any(
            not isinstance(reference, str) or not reference.strip()
            for reference in references
        ):
            issues.append(f"compatibility runtime {runtime_id} has invalid evidence_refs")
            continue
        if len(set(references)) != len(references):
            issues.append(
                f"compatibility runtime {runtime_id} has duplicate evidence_refs"
            )
        referenced_observations.extend(references)
        for field in ("cli", "surface"):
            if not isinstance(runtime.get(field), str) or not runtime[field].strip():
                issues.append(f"compatibility runtime {runtime_id} requires {field}")
        for field in ("host_checks", "core_checks"):
            checks = runtime.get(field)
            if not isinstance(checks, list) or not checks or any(
                not isinstance(check, str) or not check.strip() for check in checks
            ):
                issues.append(
                    f"compatibility runtime {runtime_id} requires non-empty {field}"
                )
        referenced = [observation_by_id.get(reference) for reference in references]
        if any(observation is None for observation in referenced):
            issues.append(
                f"compatibility runtime {runtime_id} references missing evidence"
            )
            continue
        if any(
            observation.get("runtime") != runtime_id
            for observation in referenced
            if observation is not None
        ):
            issues.append(
                f"compatibility runtime {runtime_id} references another runtime's evidence"
            )
        declared_versions: dict[str, list[str]] = {}
        invalid_versions = False
        for field in ("tested_versions", "legacy_versions"):
            versions = runtime.get(field)
            if (
                not isinstance(versions, list)
                or any(
                    not isinstance(version, str) or not version.strip()
                    for version in versions
                )
                or len(set(versions)) != len(versions)
            ):
                issues.append(
                    f"compatibility runtime {runtime_id} has invalid {field}"
                )
                invalid_versions = True
            else:
                declared_versions[field] = versions
        if invalid_versions:
            continue
        if set(declared_versions["tested_versions"]) & set(
            declared_versions["legacy_versions"]
        ):
            issues.append(
                f"compatibility runtime {runtime_id} versions cannot be tested and legacy"
            )
        live_versions = sorted(
            str(observation["version"])
            for observation in referenced
            if observation is not None
            and observation.get("status") == "live-verified"
            and isinstance(observation.get("version"), str)
        )
        legacy_versions = sorted(
            str(observation["version"])
            for observation in referenced
            if observation is not None
            and observation.get("status") == "unlinked-legacy"
            and isinstance(observation.get("version"), str)
        )
        if sorted(declared_versions["tested_versions"]) != live_versions:
            issues.append(
                f"compatibility runtime {runtime_id} tested_versions lack live evidence"
            )
        if sorted(declared_versions["legacy_versions"]) != legacy_versions:
            issues.append(
                f"compatibility runtime {runtime_id} legacy_versions lack legacy evidence"
            )
    unreferenced = sorted(set(observation_by_id) - set(referenced_observations))
    if unreferenced:
        issues.append(
            "compatibility observations are not linked to runtimes: "
            + ", ".join(unreferenced)
        )
    return issues


def load_compatibility() -> dict[str, Any]:
    try:
        content = read_control_file(
            COMPATIBILITY_PATH,
            root=COMPATIBILITY_PATH.parent,
            label="compatibility matrix",
        ).decode("utf-8")
        value = json.loads(content)
    except FileNotFoundError as exc:
        raise RuntimeContractError(f"missing compatibility matrix: {COMPATIBILITY_PATH}") from exc
    except UnsafeControlFile as exc:
        raise RuntimeContractError(str(exc)) from exc
    except OSError as exc:
        raise RuntimeContractError(f"cannot safely read compatibility matrix: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeContractError(f"invalid compatibility matrix: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeContractError("compatibility matrix must be an object")
    issues = _compatibility_issues(value)
    if issues:
        raise RuntimeContractError("invalid compatibility matrix: " + "; ".join(issues))
    return value


def _required(payload: Mapping[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is None or value == "":
        raise RuntimeContractError(f"missing runtime request field: {key}")
    return value


def _list_field(payload: Mapping[str, Any], key: str) -> list[Any]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise RuntimeContractError(f"runtime request field {key} must be a list")
    return list(value)


def _boolean_field(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key, False)
    if not isinstance(value, bool):
        raise RuntimeContractError(f"runtime request field {key} must be boolean")
    return value


def _handshake(values: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return verify_adapter_handshake(
            str(_required(values, "runtime")),
            str(_required(values, "adapter_version")),
            str(_required(values, "protocol_version")),
            values.get("project", "."),
        )
    except ValueError as exc:
        raise RuntimeContractError(str(exc)) from exc


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
        raise RuntimeContractError("on does not select a Unit; use resume --unit PATH")
    project = discover_project(values.get("project", "."))
    result = activate_session(project)
    result["active_unit_binding"] = active_unit_binding(project)
    return result


def _off(_values: Mapping[str, Any]) -> dict[str, Any]:
    return {**deactivate_session(), "active_unit_changed": False}


def _status(values: Mapping[str, Any]) -> dict[str, Any]:
    project = discover_project(values.get("project", "."))
    return {
        **build_session(project, values.get("unit")),
        "active_unit_binding": active_unit_binding(project),
    }


def _resume(values: Mapping[str, Any]) -> dict[str, Any]:
    project = discover_project(values.get("project", "."))
    result = resume_session(project, values.get("unit"))
    unit = result.get("unit")
    if isinstance(unit, dict) and unit.get("status") not in {"learned", "abandoned"}:
        result["active_unit_binding"] = bind_active_unit(
            project,
            str(unit.get("path")),
            reason="The Runtime resumed this unfinished Unit.",
        )
    else:
        result["active_unit_binding"] = active_unit_binding(project)
    return result


def _intake(values: Mapping[str, Any]) -> dict[str, Any]:
    project = discover_project(values.get("project", "."))
    binding = active_unit_binding(project)
    if binding.get("active"):
        return {
            "blocked": True,
            "reason_code": "active-unit-amendment-required",
            "active_unit": binding.get("unit"),
            "workflow": {
                "driver": "active-unit-continuation",
                "required_action": "amend",
                "new_route_allowed": False,
            },
            "next_action": (
                "record the follow-up request with amend in the active Unit; "
                "use active-unit-detach only after an explicit user decision"
            ),
        }
    return intake(values)


def _unit_migrate(values: Mapping[str, Any]) -> dict[str, Any]:
    return migrate_unit_context(
        values.get("project", "."),
        _required(values, "unit"),
    )


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


def _project_knowledge_status(values: Mapping[str, Any]) -> dict[str, Any]:
    return project_knowledge_status(values.get("project", "."))


def _project_knowledge_propose(values: Mapping[str, Any]) -> dict[str, Any]:
    entries = _list_field(values, "entries")
    if any(not isinstance(entry, dict) for entry in entries):
        raise RuntimeContractError("runtime request field entries must contain objects")
    return propose_project_knowledge(
        _required(values, "unit"),
        entries=entries,
        proposed_by=str(_required(values, "proposed_by")),
    )


def _project_knowledge_promote(values: Mapping[str, Any]) -> dict[str, Any]:
    return promote_project_knowledge(
        _required(values, "unit"),
        candidate=str(_required(values, "candidate")),
    )


def _route(values: Mapping[str, Any]) -> dict[str, Any]:
    request = RouteRequest(
        change=str(_required(values, "change")),
        risk=str(values.get("risk", "low")),
        ambiguous=_boolean_field(values, "ambiguous"),
        multi_party=_boolean_field(values, "multi_party"),
        remote=_boolean_field(values, "remote"),
        sensitive=_boolean_field(values, "sensitive"),
    )
    return classify_work(request).as_dict()


def _unit_init(values: Mapping[str, Any]) -> dict[str, Any]:
    project = discover_project(_required(values, "project"))
    with active_unit_creation_guard(project) as bind:
        path = initialize_unit(
            project,
            str(_required(values, "title")),
            values.get("output"),
            str(values.get("owner", "unassigned")),
            intent=values.get("intent"),
        )
        binding = bind(
            path,
            str(values.get("owner", "unassigned")),
            "The approved plan created this active Unit.",
        )
    return {
        "created": str(path),
        "active_unit_binding": binding,
    }


def _active_unit_detach(values: Mapping[str, Any]) -> dict[str, Any]:
    project = discover_project(values.get("project", "."))
    return detach_active_unit(
        project,
        unit=str(_required(values, "unit")),
        requested_by=str(_required(values, "requested_by")),
        reason=str(_required(values, "reason")),
    )


def _checkpoint(values: Mapping[str, Any]) -> dict[str, Any]:
    return update_checkpoint(
        _required(values, "unit"),
        completed=_list_field(values, "completed"),
        pending=_list_field(values, "pending"),
        blocked_by=_list_field(values, "blocked_by"),
        next_action=str(_required(values, "next_action")),
    )


def _amend(values: Mapping[str, Any]) -> dict[str, Any]:
    return record_unit_amendment(
        _required(values, "unit"),
        request=str(_required(values, "request")),
        reason=str(values.get("reason", "")),
        affected_artifacts=_list_field(values, "affected_artifacts"),
        requested_by=str(_required(values, "requested_by")),
    )


def _envelope_propose(values: Mapping[str, Any]) -> dict[str, Any]:
    return propose_execution_envelope(
        _required(values, "unit"),
        scope=_list_field(values, "scope"),
        stages=_list_field(values, "stages"),
        allowed_actions=_list_field(values, "allowed_actions"),
        forbidden_actions=_list_field(values, "forbidden_actions"),
        external_access=_list_field(values, "external_access"),
        max_iterations=values.get("max_iterations", 0),
        proposed_by=str(_required(values, "proposed_by")),
        expires_in_hours=values.get(
            "expires_in_hours", EXECUTION_ENVELOPE_DEFAULT_HOURS
        ),
    )


def _authorize(values: Mapping[str, Any]) -> dict[str, Any]:
    requested_action = str(_required(values, "requested_action"))
    if requested_action in {"edit", "test"}:
        return {
            "allowed": False,
            "reason_code": "core-managed-execution-required",
            "reason": (
                f"{requested_action} cannot be separated from execution; use the "
                "Core managed execution action"
            ),
        }
    return authorize_action(
        _required(values, "unit"),
        action=requested_action,
        target=values.get("target"),
        stage=values.get("stage"),
        method=values.get("method"),
        credential_ref=values.get("credential_ref"),
    )


def _managed_edit(values: Mapping[str, Any]) -> dict[str, Any]:
    return execute_managed_edit(
        _required(values, "unit"),
        changes=_list_field(values, "changes"),
    )


def _artifact_write(values: Mapping[str, Any]) -> dict[str, Any]:
    return write_unit_artifacts(
        _required(values, "unit"),
        artifacts=_list_field(values, "artifacts"),
    )


def _managed_test(values: Mapping[str, Any]) -> dict[str, Any]:
    return execute_managed_test(
        _required(values, "unit"),
        target=str(_required(values, "target")),
        command=_list_field(values, "command"),
        timeout_seconds=values.get("timeout_seconds", 300),
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
    unit = str(_required(values, "unit"))
    target = str(_required(values, "to"))
    return transition_unit(unit, target)


def _verify(values: Mapping[str, Any]) -> dict[str, Any]:
    return verify_unit(_required(values, "unit"))


ActionHandler = Callable[[Mapping[str, Any]], dict[str, Any]]
ACTION_HANDLERS: dict[str, ActionHandler] = {
    "handshake": _handshake,
    "init": _init,
    "on": _on,
    "off": _off,
    "intake": _intake,
    "status": _status,
    "resume": _resume,
    "unit-migrate": _unit_migrate,
    "inception": _inception,
    "compatibility": lambda _values: load_compatibility(),
    "catalog-status": lambda _values: load_catalog(),
    "release-check": _release_check,
    "foundation-decision": _foundation_decision,
    "foundation-evidence": _foundation_evidence,
    "foundation-promote": _foundation_promote,
    "project-knowledge-status": _project_knowledge_status,
    "project-knowledge-propose": _project_knowledge_propose,
    "project-knowledge-promote": _project_knowledge_promote,
    "route": _route,
    "unit-init": _unit_init,
    "active-unit-detach": _active_unit_detach,
    "checkpoint": _checkpoint,
    "amend": _amend,
    "envelope-propose": _envelope_propose,
    "envelope-approve": lambda values: approve_execution_envelope(
        _required(values, "unit")
    ),
    "authorize": _authorize,
    "managed-edit": _managed_edit,
    "artifact-write": _artifact_write,
    "managed-test": _managed_test,
    "evidence": _evidence,
    "decision": _decision,
    "transition": _transition,
    "verify": _verify,
}


_UNIT_BOUND_ACTIONS = {
    "unit-migrate",
    "checkpoint",
    "amend",
    "envelope-propose",
    "envelope-approve",
    "authorize",
    "managed-edit",
    "artifact-write",
    "managed-test",
    "evidence",
    "decision",
    "transition",
    "project-knowledge-propose",
    "project-knowledge-promote",
}
_READ_ONLY_UNIT_ACTIONS = {"verify"}
_PROJECT_ROUTE_ACTIONS = {"inception", "route"}


def _guard_active_unit(action: str, values: Mapping[str, Any]) -> None:
    if action in _UNIT_BOUND_ACTIONS | _READ_ONLY_UNIT_ACTIONS:
        unit = Path(str(_required(values, "unit"))).expanduser().resolve()
        if not unit.is_dir() or not (unit / "unit.json").is_file():
            return  # The action handler reports its own payload/path error.
        project = project_manifest_for_unit(unit)
        require_active_unit_match(
            project,
            unit,
            action=action,
            bind_if_empty=action in _UNIT_BOUND_ACTIONS,
        )
    elif action in _PROJECT_ROUTE_ACTIONS:
        project = discover_project(values.get("project", "."))
        binding = active_unit_binding(project)
        if binding.get("active"):
            active = binding.get("unit") or {}
            raise RuntimeContractError(
                f"{action} blocked by unfinished active Unit {active.get('unit_id')}; "
                "record the request with amend"
            )


def execute_action(action: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    handler = ACTION_HANDLERS.get(action)
    if handler is None:
        raise RuntimeContractError(f"unsupported runtime action: {action}")
    values = dict(payload or {})
    if action in _UNIT_BOUND_ACTIONS:
        unit = Path(str(_required(values, "unit"))).expanduser().resolve()
        if unit.is_dir() and (unit / "unit.json").is_file():
            project = project_manifest_for_unit(unit)
            with active_unit_action_guard(project, unit, action=action):
                result = handler(values)
            if action == "transition" and values.get("to") in {"learned", "abandoned"}:
                result["active_unit_binding"] = complete_active_unit(project, unit)
            return result
    if action not in {"on", "off", "intake", "resume", "unit-init", "active-unit-detach"}:
        _guard_active_unit(action, values)
    return handler(values)
