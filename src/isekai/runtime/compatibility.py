from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from ..support.files import UnsafeControlFile, read_control_file
from .catalog_contract import catalog_model_issues
from .request_fields import RuntimeContractError


COMPATIBILITY_PATH = Path(__file__).resolve().parents[1] / "data/compatibility.json"
COMPATIBILITY_OBSERVATION_STATUSES = {
    "live-verified", "validation-only", "unavailable", "unlinked-legacy",
}


@dataclass(frozen=True)
class CompatibilityCollections:
    runtimes: list[Any]
    observations: list[Any]


class ControlFileReader(Protocol):
    def __call__(
        self,
        path: str | Path,
        *,
        root: str | Path | None = None,
        label: str = "control file",
    ) -> bytes: ...


def _contract_issues(
    value: dict[str, Any],
) -> tuple[list[str], CompatibilityCollections | None]:
    issues: list[str] = []
    if value.get("schema_version") != "1.0.0":
        issues.append("compatibility matrix has an unsupported schema_version")
    if value.get("protocol_version") != "1.2.0":
        issues.append("compatibility matrix has an unsupported protocol_version")
    runtime_contract = value.get("runtime_contract")
    if not isinstance(runtime_contract, dict):
        issues.append("compatibility runtime_contract must be an object")
    else:
        expected_fields = {
            "high_risk_actions": [],
            "human_decision_actions": [
                "amend", "active-unit-detach", "decision",
                "foundation-decision", "foundation-promote",
            ],
            "external_agent_actions": ["external-api"],
            "credential_handling": "opaque-reference-resolved-by-host",
        }
        for field, expected in expected_fields.items():
            if runtime_contract.get(field) != expected:
                label = field if field != "high_risk_actions" else "high-risk actions"
                if field == "high_risk_actions":
                    issues.append(
                        "compatibility runtime_contract cannot allow high-risk actions"
                    )
                else:
                    issues.append(
                        f"compatibility runtime_contract has invalid {label}"
                    )
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
    if value.get("trust_model") != expected_trust_model:
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
        return ["compatibility runtimes must be a non-empty list"], None
    if not isinstance(observations, list):
        return ["compatibility observations must be a list"], None
    return issues, CompatibilityCollections(runtimes, observations)


def _observation_issues(
    observations: list[Any],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    issues: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            issues.append(f"compatibility observation {index} must be an object")
            continue
        observation_id = observation.get("id")
        if not isinstance(observation_id, str) or not observation_id.strip():
            issues.append(f"compatibility observation {index} requires id")
        elif observation_id in by_id:
            issues.append(f"duplicate compatibility observation: {observation_id}")
        else:
            by_id[observation_id] = observation
        status = observation.get("status")
        if not isinstance(status, str) or status not in COMPATIBILITY_OBSERVATION_STATUSES:
            issues.append(f"compatibility observation {index} has invalid status")
        for field in ("runtime", "evidence_strength", "source_ref"):
            if not isinstance(observation.get(field), str) or not observation[field].strip():
                issues.append(f"compatibility observation {index} requires {field}")
        version = observation.get("version")
        if version is not None and (not isinstance(version, str) or not version.strip()):
            issues.append(f"compatibility observation {index} has invalid version")
        observed_on = observation.get("observed_on")
        if observed_on is not None:
            try:
                date.fromisoformat(observed_on)
            except (TypeError, ValueError):
                issues.append(f"compatibility observation {index} has invalid observed_on")
        if status == "unlinked-legacy" and observed_on is not None:
            issues.append(
                f"compatibility observation {index} legacy evidence cannot claim observed_on"
            )
        if status in {"live-verified", "validation-only", "unavailable"} and observed_on is None:
            issues.append(f"compatibility observation {index} requires observed_on")
        if status in {"live-verified", "validation-only", "unlinked-legacy"} and (
            not isinstance(version, str) or not version.strip()
        ):
            issues.append(f"compatibility observation {index} status requires version")
        checks = observation.get("checks")
        if not isinstance(checks, list) or not checks or any(
            not isinstance(check, str) or not check.strip() for check in checks
        ):
            issues.append(
                f"compatibility observation {index} requires non-empty checks"
            )
    return issues, by_id


def _declared_versions(
    runtime: dict[str, Any],
    runtime_id: str,
) -> tuple[dict[str, list[str]], list[str]]:
    declared: dict[str, list[str]] = {}
    issues: list[str] = []
    for field in ("tested_versions", "legacy_versions"):
        versions = runtime.get(field)
        if (
            not isinstance(versions, list)
            or any(not isinstance(version, str) or not version.strip() for version in versions)
            or len(set(versions)) != len(versions)
        ):
            issues.append(f"compatibility runtime {runtime_id} has invalid {field}")
        else:
            declared[field] = versions
    return declared, issues


def _runtime_issues(
    runtimes: list[Any],
    observations: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    seen: set[str] = set()
    referenced_ids: list[str] = []
    for index, runtime in enumerate(runtimes):
        if not isinstance(runtime, dict):
            issues.append(f"compatibility runtime {index} must be an object")
            continue
        runtime_id = runtime.get("id")
        if not isinstance(runtime_id, str) or not runtime_id.strip():
            issues.append(f"compatibility runtime {index} requires id")
            continue
        if runtime_id in seen:
            issues.append(f"duplicate compatibility runtime: {runtime_id}")
        seen.add(runtime_id)
        references = runtime.get("evidence_refs")
        if not isinstance(references, list) or any(
            not isinstance(reference, str) or not reference.strip()
            for reference in references
        ):
            issues.append(f"compatibility runtime {runtime_id} has invalid evidence_refs")
            continue
        if len(set(references)) != len(references):
            issues.append(f"compatibility runtime {runtime_id} has duplicate evidence_refs")
        referenced_ids.extend(references)
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
        referenced = [observations.get(reference) for reference in references]
        if any(observation is None for observation in referenced):
            issues.append(f"compatibility runtime {runtime_id} references missing evidence")
            continue
        if any(
            observation.get("runtime") != runtime_id
            for observation in referenced
            if observation is not None
        ):
            issues.append(
                f"compatibility runtime {runtime_id} references another runtime's evidence"
            )
        declared, version_issues = _declared_versions(runtime, runtime_id)
        issues.extend(version_issues)
        if version_issues:
            continue
        if set(declared["tested_versions"]) & set(declared["legacy_versions"]):
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
        if sorted(declared["tested_versions"]) != live_versions:
            issues.append(
                f"compatibility runtime {runtime_id} tested_versions lack live evidence"
            )
        if sorted(declared["legacy_versions"]) != legacy_versions:
            issues.append(
                f"compatibility runtime {runtime_id} legacy_versions lack legacy evidence"
            )
    return issues, referenced_ids


def compatibility_issues(value: dict[str, Any]) -> list[str]:
    issues, collections = _contract_issues(value)
    if collections is None:
        return issues
    observation_issues, observations = _observation_issues(collections.observations)
    runtime_issues, referenced = _runtime_issues(collections.runtimes, observations)
    issues.extend(observation_issues)
    issues.extend(runtime_issues)
    unreferenced = sorted(set(observations) - set(referenced))
    if unreferenced:
        issues.append(
            "compatibility observations are not linked to runtimes: "
            + ", ".join(unreferenced)
        )
    return issues


def load_compatibility(
    reader: ControlFileReader = read_control_file,
) -> dict[str, Any]:
    try:
        content = reader(
            COMPATIBILITY_PATH,
            root=COMPATIBILITY_PATH.parent,
            label="compatibility matrix",
        ).decode("utf-8")
        value = json.loads(content)
    except FileNotFoundError as exc:
        raise RuntimeContractError(
            f"missing compatibility matrix: {COMPATIBILITY_PATH}"
        ) from exc
    except UnsafeControlFile as exc:
        raise RuntimeContractError(str(exc)) from exc
    except OSError as exc:
        raise RuntimeContractError(f"cannot safely read compatibility matrix: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeContractError(f"invalid compatibility matrix: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeContractError("compatibility matrix must be an object")
    issues = compatibility_issues(value)
    if issues:
        raise RuntimeContractError("invalid compatibility matrix: " + "; ".join(issues))
    return value


__all__ = ["compatibility_issues", "load_compatibility"]
