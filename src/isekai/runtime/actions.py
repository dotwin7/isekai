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
    detach_active_unit,
    project_manifest_for_unit,
)
from ..workflow.authorization import authorize_action
from ..workflow import initialize_project, load_catalog
from ..workflow.catalog import select_active_entries
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
    propose_execution_envelope,
)
from isekai.catalog.ai_dlc.unit.initialization import initialize_unit
from isekai.catalog.ai_dlc.unit.lifecycle import transition_unit, verify_unit
from isekai.catalog.ai_dlc.unit.managed_execution import (
    execute_managed_edit,
    execute_proof,
    write_unit_artifacts,
)
from .active_guard import (
    UNIT_BOUND_ACTIONS as _UNIT_BOUND_ACTIONS,
    guard_active_unit as _guard_active_unit,
)
from .catalog_contract import catalog_model_issues
from .request_fields import (
    RuntimeContractError,
    boolean_field as _boolean_field,
    list_field as _list_field,
    optional_string_field as _optional_string_field,
    string_field as _string_field,
    string_list_field as _string_list_field,
)

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
    if value.get("protocol_version") != "1.2.0":
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
        "action_execution": "core-managed-edit-and-proof",
        "proof_isolation": "os-enforced-source-and-user-data-read-denial-write-confinement-network-denial-fail-closed",
        "conversation_change_reporting": "runtime-adapter-attested-not-core-observed",
        "human_identity": "caller-attested-not-core-verified",
        "evidence_execution": "core-receipted-for-proofs",
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
        if not isinstance(status, str) or status not in COMPATIBILITY_OBSERVATION_STATUSES:
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
        if isinstance(status, str) and status in {
            "live-verified",
            "validation-only",
            "unavailable",
        } and (
            observed_on is None
        ):
            issues.append(f"compatibility observation {index} requires observed_on")
        if isinstance(status, str) and status in {
            "live-verified",
            "validation-only",
            "unlinked-legacy",
        } and (
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


def _handshake(values: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return verify_adapter_handshake(
            _string_field(values, "runtime"),
            _string_field(values, "adapter_version"),
            _string_field(values, "protocol_version"),
            _string_field(values, "project", default="."),
        )
    except ValueError as exc:
        raise RuntimeContractError(str(exc)) from exc


def _init(values: Mapping[str, Any]) -> dict[str, Any]:
    path = initialize_project(
        _string_field(values, "path", default="."),
        project_id=_optional_string_field(values, "project_id"),
        foundation_path=_optional_string_field(values, "foundation_path"),
        profiles=_string_list_field(values, "profiles"),
        document_language=_string_field(values, "document_language", default="ko"),
        maximum_agent_level=_string_field(values, "maximum_agent_level", default="L0"),
    )
    return {"created": str(path), "units": str(path.parent / "units")}


def _on(values: Mapping[str, Any]) -> dict[str, Any]:
    if values.get("unit") is not None and values.get("unit") != "":
        raise RuntimeContractError("on does not select a Unit; use resume --unit PATH")
    project = discover_project(_string_field(values, "project", default="."))
    result = activate_session(project)
    result["active_unit_binding"] = active_unit_binding(project)
    return result


def _off(_values: Mapping[str, Any]) -> dict[str, Any]:
    return {**deactivate_session(), "active_unit_changed": False}


def _status(values: Mapping[str, Any]) -> dict[str, Any]:
    project = discover_project(_string_field(values, "project", default="."))
    return {
        **build_session(project, _optional_string_field(values, "unit")),
        "active_unit_binding": active_unit_binding(project),
    }


def _resume(values: Mapping[str, Any]) -> dict[str, Any]:
    project = discover_project(_string_field(values, "project", default="."))
    result = resume_session(project, _optional_string_field(values, "unit"))
    unit = result.get("unit")
    if isinstance(unit, dict) and (
        not isinstance(unit.get("status"), str)
        or unit.get("status") not in {"learned", "abandoned"}
    ):
        result["active_unit_binding"] = bind_active_unit(
            project,
            str(unit.get("path")),
            reason="The Runtime resumed this unfinished Unit.",
        )
    else:
        result["active_unit_binding"] = active_unit_binding(project)
    return result


def _intake(values: Mapping[str, Any]) -> dict[str, Any]:
    project = discover_project(_string_field(values, "project", default="."))
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
    selection = select_active_entries(load_catalog())
    if not any("intake" in s.get("actions", []) for s in selection["catalog_entries"] if s["active"]):
        raise RuntimeContractError("no active catalog entry supports the intake action")
    return intake(values)


def _unit_migrate(values: Mapping[str, Any]) -> dict[str, Any]:
    return migrate_unit_context(
        _string_field(values, "project", default="."),
        _string_field(values, "unit"),
    )


def _inception(values: Mapping[str, Any]) -> dict[str, Any]:
    return inception_session(_string_field(values, "project", default="."))


def _release_check(values: Mapping[str, Any]) -> dict[str, Any]:
    return load_foundation(
        _string_field(values, "foundation", default="foundation")
    ).readiness()


def _foundation_decision(values: Mapping[str, Any]) -> dict[str, Any]:
    return record_foundation_decision(
        _string_field(values, "foundation", default="foundation"),
        outcome=_string_field(values, "outcome"),
        summary=_string_field(values, "summary"),
        decided_by=_string_field(values, "decided_by"),
    )


def _foundation_evidence(values: Mapping[str, Any]) -> dict[str, Any]:
    return record_foundation_evidence(
        _string_field(values, "foundation", default="foundation"),
        passed=_boolean_field(values, "passed"),
        checks=_list_field(values, "checks"),
        scope=_string_field(values, "scope"),
        recorded_by=_string_field(values, "recorded_by"),
    )


def _foundation_promote(values: Mapping[str, Any]) -> dict[str, Any]:
    return promote_foundation(
        _string_field(values, "foundation", default="foundation")
    )


def _project_knowledge_status(values: Mapping[str, Any]) -> dict[str, Any]:
    return project_knowledge_status(
        _string_field(values, "project", default=".")
    )


def _project_knowledge_propose(values: Mapping[str, Any]) -> dict[str, Any]:
    entries = _list_field(values, "entries")
    if any(not isinstance(entry, dict) for entry in entries):
        raise RuntimeContractError("runtime request field entries must contain objects")
    return propose_project_knowledge(
        _string_field(values, "unit"),
        entries=entries,
        proposed_by=_string_field(values, "proposed_by"),
    )


def _project_knowledge_promote(values: Mapping[str, Any]) -> dict[str, Any]:
    return promote_project_knowledge(
        _string_field(values, "unit"),
        candidate=_string_field(values, "candidate"),
    )


def _route(values: Mapping[str, Any]) -> dict[str, Any]:
    request = RouteRequest(
        change=_string_field(values, "change"),
        risk=_string_field(values, "risk", default="low"),
        ambiguous=_boolean_field(values, "ambiguous", default=False),
        multi_party=_boolean_field(values, "multi_party", default=False),
        remote=_boolean_field(values, "remote", default=False),
        sensitive=_boolean_field(values, "sensitive", default=False),
    )
    return classify_work(request).as_dict()


def _resolve_catalog_entry(values: Mapping[str, Any]) -> str:
    entry = values.get("catalog_entry")
    if entry is not None:
        return _string_field(values, "catalog_entry")
    selected = select_active_entries(load_catalog())["selected_entry"]
    if selected is None:
        raise RuntimeContractError("multiple active catalog entries; catalog_entry is required")
    return str(selected)


def _unit_init(values: Mapping[str, Any]) -> dict[str, Any]:
    project = discover_project(_string_field(values, "project"))
    catalog_entry = _resolve_catalog_entry(values)
    owner = _string_field(values, "owner", default="unassigned")
    output = _optional_string_field(values, "output")
    intent = values.get("intent")
    if intent is not None and not isinstance(intent, dict):
        raise RuntimeContractError("runtime request field intent must be an object")
    with active_unit_creation_guard(project) as bind:
        binding: dict[str, Any] | None = None

        def bind_created(path: Path) -> None:
            nonlocal binding
            binding = bind(
                path,
                owner,
                "The approved plan created this active Unit.",
            )

        path = initialize_unit(
            project,
            _string_field(values, "title"),
            output,
            owner,
            intent=intent,
            catalog_entry=catalog_entry,
            _postflight=bind_created,
        )
        if binding is None:  # pragma: no cover - initialize_unit always calls postflight
            raise RuntimeContractError("Unit initialization did not bind its Unit")
    return {
        "created": str(path),
        "catalog_entry": str(catalog_entry),
        "active_unit_binding": binding,
    }


def _active_unit_detach(values: Mapping[str, Any]) -> dict[str, Any]:
    project = discover_project(_string_field(values, "project", default="."))
    return detach_active_unit(
        project,
        unit=_string_field(values, "unit"),
        requested_by=_string_field(values, "requested_by"),
        reason=_string_field(values, "reason"),
    )


def _checkpoint(values: Mapping[str, Any]) -> dict[str, Any]:
    return update_checkpoint(
        _string_field(values, "unit"), completed=_string_list_field(values, "completed"),
        pending=_string_list_field(values, "pending"), blocked_by=_string_list_field(values, "blocked_by"),
        next_action=_string_field(values, "next_action"),
    )


def _amend(values: Mapping[str, Any]) -> dict[str, Any]:
    return record_unit_amendment(
        _string_field(values, "unit"), request=_string_field(values, "request"),
        reason=_string_field(values, "reason", default="", allow_empty=True),
        affected_artifacts=_string_list_field(values, "affected_artifacts"),
        requested_by=_string_field(values, "requested_by"),
    )


def _envelope_propose(values: Mapping[str, Any]) -> dict[str, Any]:
    return propose_execution_envelope(
        _string_field(values, "unit"), scope=_string_list_field(values, "scope"),
        stages=_list_field(values, "stages"),
        allowed_actions=_string_list_field(values, "allowed_actions"),
        forbidden_actions=_string_list_field(values, "forbidden_actions"),
        external_access=_list_field(values, "external_access"),
        max_iterations=values.get("max_iterations", 0),
        proposed_by=_string_field(values, "proposed_by"),
        expires_in_hours=values.get("expires_in_hours", EXECUTION_ENVELOPE_DEFAULT_HOURS),
    )


def _authorize(values: Mapping[str, Any]) -> dict[str, Any]:
    return authorize_action(
        _string_field(values, "unit"), action=_string_field(values, "requested_action"),
        target=_optional_string_field(values, "target"),
        stage=_optional_string_field(values, "stage"),
        method=_optional_string_field(values, "method"),
        credential_ref=_optional_string_field(values, "credential_ref"),
    )


def _managed_edit(values: Mapping[str, Any]) -> dict[str, Any]:
    return execute_managed_edit(
        _string_field(values, "unit"),
        changes=_list_field(values, "changes"),
    )


def _artifact_write(values: Mapping[str, Any]) -> dict[str, Any]:
    return write_unit_artifacts(
        _string_field(values, "unit"),
        artifacts=_list_field(values, "artifacts"),
    )


def _prove(values: Mapping[str, Any]) -> dict[str, Any]:
    return execute_proof(
        _string_field(values, "unit"), target=_string_field(values, "target"),
        command=_list_field(values, "command"), timeout_seconds=values.get("timeout_seconds", 300),
    )


def _evidence(values: Mapping[str, Any]) -> dict[str, Any]:
    return record_evidence(
        _string_field(values, "unit"),
        passed=_boolean_field(values, "passed"),
        commands=_list_field(values, "commands"),
        scope=_string_field(values, "scope"),
        recorded_by=_string_field(values, "recorded_by"),
        notes=_string_field(values, "notes", default="", allow_empty=True),
    )


def _decision(values: Mapping[str, Any]) -> dict[str, Any]:
    return record_decision(
        _string_field(values, "unit"),
        gate=_string_field(values, "gate"),
        outcome=_string_field(values, "outcome"),
        summary=_string_field(values, "summary"),
        rationale=_string_list_field(values, "rationale"),
        alternatives=_list_field(values, "alternatives"),
        tradeoffs=_string_list_field(values, "tradeoffs"),
        risks=_string_list_field(values, "risks"),
        references=_string_list_field(values, "references"),
        decided_by=_string_field(values, "decided_by"),
    )


def _transition(values: Mapping[str, Any]) -> dict[str, Any]:
    return transition_unit(
        _string_field(values, "unit"),
        _string_field(values, "to"),
    )


def _verify(values: Mapping[str, Any]) -> dict[str, Any]:
    return verify_unit(_string_field(values, "unit"))


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
        _string_field(values, "unit")
    ),
    "authorize": _authorize,
    "managed-edit": _managed_edit,
    "artifact-write": _artifact_write,
    "prove": _prove,
    "evidence": _evidence,
    "decision": _decision,
    "transition": _transition,
    "verify": _verify,
}


def execute_action(action: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(action, str):
        raise RuntimeContractError("runtime action must be a string")
    if payload is not None and not isinstance(payload, Mapping):
        raise RuntimeContractError("runtime request payload must be an object")
    handler = ACTION_HANDLERS.get(action)
    if handler is None:
        raise RuntimeContractError(f"unsupported runtime action: {action}")
    values = dict(payload or {})
    if action in _UNIT_BOUND_ACTIONS:
        unit = Path(_string_field(values, "unit")).expanduser().resolve()
        if unit.is_dir() and (unit / "unit.json").is_file():
            project = project_manifest_for_unit(unit)
            with active_unit_action_guard(project, unit, action=action) as complete:
                result = handler(values)
                transition_target = values.get("to")
                if action == "transition" and isinstance(
                    transition_target, str
                ) and transition_target in {
                    "learned",
                    "abandoned",
                }:
                    result["active_unit_binding"] = complete()
            return result
    if action not in {"on", "off", "intake", "resume", "unit-init", "active-unit-detach"}:
        _guard_active_unit(action, values)
    return handler(values)
