from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .types import (
    ALLOWED_ASSET_KINDS,
    ALLOWED_STATUSES,
    CONDITION_TYPES,
    EVALUATOR_TYPES,
    REQUIRED_ASSET_FIELDS,
    FoundationError,
    FoundationRelease,
)
from ..support.files import UnsafeControlFile, read_control_file
from ..support.jsonio import UnsafeWritePath, write_json_atomic, write_json_atomic_beneath


def _load_json(
    path: Path,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    try:
        content = read_control_file(
            path,
            root=root or path.parent,
            label="Foundation control file",
        )
    except FileNotFoundError as exc:
        raise FoundationError(f"missing file: {path}") from exc
    except (OSError, UnsafeControlFile) as exc:
        raise FoundationError(f"unsafe Foundation control file {path}: {exc}") from exc
    try:
        value = json.loads(content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise FoundationError(f"invalid UTF-8 in {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise FoundationError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FoundationError(f"expected JSON object: {path}")
    return value


def _write_json(
    path: Path,
    value: dict[str, Any],
    *,
    root: Path | None = None,
) -> None:
    if root is not None:
        try:
            relative = path.relative_to(root)
            write_json_atomic_beneath(root, relative, value)
        except (ValueError, UnsafeWritePath) as exc:
            raise FoundationError(
                f"unsafe Foundation control write target {path}: {exc}"
            ) from exc
        return
    write_json_atomic(path, value)


def _optional_json(
    path: Path,
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FoundationError(
            f"cannot inspect optional Foundation control file {path}: {exc}"
        ) from exc
    return _load_json(path, root=root)

def _safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise FoundationError("path must be a non-empty relative path")
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise FoundationError(f"unsafe path: {relative}")
    candidate = root / raw
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise FoundationError(f"path escapes Foundation root: {relative}") from exc
    return candidate


def _require_fields(value: dict[str, Any], fields: set[str], label: str) -> None:
    missing = sorted(fields - value.keys())
    if missing:
        raise FoundationError(f"{label} missing fields: {', '.join(missing)}")


def _reference_id(reference: Any, label: str) -> str:
    if isinstance(reference, str) and reference.strip():
        raise FoundationError(f"{label} must pin a parent version: {reference}")
    if not isinstance(reference, dict):
        raise FoundationError(f"{label} must be a pinned reference object")
    _require_fields(reference, {"id", "version"}, label)
    if not all(isinstance(reference[key], str) and reference[key].strip() for key in ("id", "version")):
        raise FoundationError(f"{label} id and version must be non-empty strings")
    return str(reference["id"])


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise FoundationError(f"{label} must be a concrete ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FoundationError(f"{label} must be a concrete ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _validate_provenance_record(provenance: Any, label: str) -> None:
    if not isinstance(provenance, dict):
        raise FoundationError(f"{label} must be an object")
    _require_fields(provenance, {"source", "recorded_by", "recorded_at"}, label)
    for key in ("source", "recorded_by"):
        if not isinstance(provenance[key], str) or not provenance[key].strip() or "<" in provenance[key]:
            raise FoundationError(f"{label} {key} must be concrete")
    _parse_timestamp(provenance["recorded_at"], f"{label} recorded_at")


def _validate_provenance(asset: dict[str, Any]) -> None:
    _validate_provenance_record(asset.get("provenance"), f"{asset['id']} provenance")
    for key in ("owner", "classification", "scope", "schema_version", "version"):
        if not isinstance(asset.get(key), str) or not asset[key].strip():
            raise FoundationError(f"{asset['id']} requires a non-empty {key}")
    if asset["schema_version"] != "1.0.0":
        raise FoundationError(f"{asset['id']} has an unsupported schema_version")


def _validate_rule_metadata(rule: dict[str, Any], label: str) -> None:
    _require_fields(rule, {"owner", "provenance", "applies_to"}, label)
    if not isinstance(rule["owner"], str) or not rule["owner"].strip():
        raise FoundationError(f"{label} requires an owner")
    _validate_provenance_record(rule["provenance"], f"{label} provenance")
    applies_to = rule["applies_to"]
    if not isinstance(applies_to, list) or not applies_to or any(
        not isinstance(item, str) or not item.strip() for item in applies_to
    ):
        raise FoundationError(f"{label} requires non-empty applies_to values")
    allowed_targets = {"*", "foundation", "query", "quick-change", "unit"}
    unknown_targets = sorted(set(applies_to) - allowed_targets)
    if unknown_targets:
        raise FoundationError(
            f"{label} has unsupported applies_to values: "
            + ", ".join(unknown_targets)
        )
    if len(set(applies_to)) != len(applies_to):
        raise FoundationError(f"{label} applies_to must not contain duplicates")


def _require_condition_fields(condition: dict[str, Any], fields: set[str], rule_id: str) -> None:
    _require_fields(condition, fields, f"{rule_id} condition")


def _require_condition_strings(condition: dict[str, Any], fields: set[str], rule_id: str) -> None:
    for field in fields:
        if not isinstance(condition.get(field), str) or not condition[field].strip():
            raise FoundationError(f"{rule_id} condition {field} must be a non-empty string")


def _validate_condition(condition: Any, rule_id: str) -> None:
    if (
        not isinstance(condition, dict)
        or not isinstance(condition.get("type"), str)
        or condition.get("type") not in CONDITION_TYPES
    ):
        raise FoundationError(f"{rule_id} has an unsupported condition")
    condition_type = condition["type"]
    if condition_type == "extension-cannot-weaken-must":
        _require_condition_fields(condition, {"parent_asset", "parent_rule_id", "parent_level", "comparison"}, rule_id)
        _require_condition_strings(condition, {"parent_asset", "parent_rule_id", "parent_level", "comparison"}, rule_id)
        if condition["parent_level"] != "MUST" or condition["comparison"] not in {"preserve-or-strengthen", "equal-or-stronger"}:
            raise FoundationError(f"{rule_id} extension condition requires a MUST parent and valid comparison")
    elif condition_type == "required-artifact":
        _require_condition_fields(condition, {"artifact", "field", "equals"}, rule_id)
        if not all(isinstance(condition[key], str) and condition[key].strip() for key in ("artifact", "field")):
            raise FoundationError(f"{rule_id} required-artifact paths must be non-empty")
        if "commands_required" in condition and not isinstance(condition["commands_required"], bool):
            raise FoundationError(f"{rule_id} commands_required must be boolean")
    elif condition_type == "context-scope":
        fields = condition.get("required_fields")
        if not isinstance(fields, list) or not fields or any(not isinstance(item, str) or not item for item in fields):
            raise FoundationError(f"{rule_id} context-scope needs required_fields")
        if "allowed_routes" in condition:
            routes = condition["allowed_routes"]
            if (
                not isinstance(routes, list)
                or not routes
                or any(
                    not isinstance(route, str)
                    or route not in {"query", "quick-change", "unit"}
                    for route in routes
                )
            ):
                raise FoundationError(
                    f"{rule_id} context-scope allowed_routes must contain supported routes"
                )
    elif condition_type == "required-decision":
        _require_condition_fields(condition, {"gate", "decision_ref", "outcome", "decided_by", "scope"}, rule_id)
        _require_condition_strings(condition, {"gate", "decision_ref", "outcome", "decided_by", "scope"}, rule_id)
    elif condition_type == "required-envelope":
        _require_condition_fields(condition, {"envelope_ref", "action", "target_scope", "stage", "expires_at"}, rule_id)
        _require_condition_strings(condition, {"envelope_ref", "action", "target_scope", "stage", "expires_at"}, rule_id)
        _parse_timestamp(condition["expires_at"], f"{rule_id} condition expires_at")
    elif condition_type == "required-external-authorization":
        fields = {
            "minimum_agent_level",
            "action",
            "credential_access",
            "environment",
            "target",
            "budget",
            "secret_resolution",
        }
        _require_condition_fields(condition, fields, rule_id)
        _require_condition_strings(condition, fields, rule_id)
        if condition["minimum_agent_level"] != "L2":
            raise FoundationError(
                f"{rule_id} external authorization minimum level must be L2"
            )
        if condition["action"] != "external-api":
            raise FoundationError(
                f"{rule_id} external authorization action must be external-api"
            )
        if condition["credential_access"] != "forbidden":
            raise FoundationError(
                f"{rule_id} external authorization must forbid credential access"
            )
    elif condition_type == "required-lineage":
        _require_condition_fields(condition, {"mapping_ref", "source_ref", "target_ref", "transformation", "raw_reference"}, rule_id)
        _require_condition_strings(condition, {"mapping_ref", "source_ref", "target_ref", "transformation", "raw_reference"}, rule_id)
    elif condition_type == "required-promotion-review":
        _require_condition_fields(condition, {"entry_ref", "evidence_refs", "reviewed_by", "effective_from", "expires_at", "promotion_decision_ref"}, rule_id)
        _require_condition_strings(condition, {"entry_ref", "reviewed_by", "effective_from", "expires_at", "promotion_decision_ref"}, rule_id)
        _parse_timestamp(condition["effective_from"], f"{rule_id} condition effective_from")
        _parse_timestamp(condition["expires_at"], f"{rule_id} condition expires_at")
        evidence_refs = condition["evidence_refs"]
        if not isinstance(evidence_refs, list) or not evidence_refs or any(
            not isinstance(item, str) or not item.strip() for item in evidence_refs
        ):
            raise FoundationError(
                f"{rule_id} evidence_refs must be a non-empty list of strings"
            )
        if len(set(evidence_refs)) != len(evidence_refs):
            raise FoundationError(f"{rule_id} evidence_refs must not contain duplicates")
    elif condition_type == "required-exception-controls":
        _require_condition_fields(condition, {"rule_ref", "reason", "owner", "scope", "compensating_controls", "expires_at", "review_ref", "decision_ref"}, rule_id)
        _require_condition_strings(condition, {"rule_ref", "reason", "owner", "scope", "expires_at", "review_ref", "decision_ref"}, rule_id)
        _parse_timestamp(condition["expires_at"], f"{rule_id} condition expires_at")
        if not isinstance(condition["compensating_controls"], list) or not condition["compensating_controls"] or any(not isinstance(item, str) or not item.strip() for item in condition["compensating_controls"]):
            raise FoundationError(f"{rule_id} compensating_controls must be a non-empty list of strings")
    elif condition_type == "required-dod":
        _require_condition_fields(condition, {"unit_ref", "required_artifacts", "evaluation_refs", "evidence_ref"}, rule_id)
        _require_condition_strings(condition, {"unit_ref", "evidence_ref"}, rule_id)
        for field in ("required_artifacts", "evaluation_refs"):
            values = condition[field]
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(item, str) or not item.strip() for item in values)
                or len(set(values)) != len(values)
            ):
                raise FoundationError(
                    f"{rule_id} {field} must be a unique non-empty string list"
                )


def _validate_rules(asset: dict[str, Any]) -> None:
    rules = asset["content"].get("rules")
    if not isinstance(rules, list) or not rules:
        raise FoundationError(f"{asset['id']} requires rules")
    seen: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or not rule["id"].strip():
            raise FoundationError(f"{asset['id']} has an invalid rule")
        if rule["id"] in seen:
            raise FoundationError(f"duplicate rule id: {rule['id']}")
        seen.add(rule["id"])
        if not isinstance(rule.get("level"), str) or rule.get("level") not in {
            "MUST",
            "SHOULD",
            "MAY",
        }:
            raise FoundationError(f"{rule['id']} has an invalid level")
        _validate_rule_metadata(rule, rule["id"])
        condition = rule.get("condition")
        if rule["level"] == "MUST" and not isinstance(condition, dict):
            raise FoundationError(f"{rule['id']} MUST rule requires a condition")
        if condition is not None:
            _validate_condition(condition, rule["id"])


def _validate_asset_specific(root: Path, asset: dict[str, Any]) -> None:
    kind = asset["kind"]
    content = asset["content"]
    if not isinstance(content, dict):
        raise FoundationError(f"{asset['id']} content must be an object")
    if kind == "gate-matrix":
        _require_fields(content, {"matrix_version", "gates"}, asset["id"])
        if content["matrix_version"] != asset["version"]:
            raise FoundationError(f"{asset['id']} matrix_version must match asset version")
        gates = content["gates"]
        if not isinstance(gates, list) or not gates:
            raise FoundationError(f"{asset['id']} requires gates")
        seen: set[str] = set()
        for gate in gates:
            if not isinstance(gate, dict):
                raise FoundationError(f"{asset['id']} gate must be an object")
            _require_fields(gate, {"id", "trigger", "required_decision", "accountable_role"}, asset["id"])
            if any(not isinstance(gate[key], str) or not gate[key].strip() for key in ("id", "trigger", "accountable_role")):
                raise FoundationError(f"{asset['id']} gate metadata must be non-empty")
            if not isinstance(gate["required_decision"], bool):
                raise FoundationError(f"{asset['id']} required_decision must be boolean")
            if gate["id"] in seen:
                raise FoundationError(f"{asset['id']} has duplicate gate: {gate['id']}")
            seen.add(gate["id"])
    elif kind in {"profile", "extension"}:
        namespace = content.get("namespace")
        if not isinstance(namespace, str) or not namespace.strip():
            raise FoundationError(f"{asset['id']} requires a namespace")
    elif kind == "rule-set":
        _validate_rules(asset)
    elif kind == "policy":
        if not isinstance(content.get("effect"), str) or content.get(
            "effect"
        ) not in {"allow", "deny"}:
            raise FoundationError(f"{asset['id']} has an invalid policy effect")
        if not isinstance(content.get("when"), dict):
            raise FoundationError(f"{asset['id']} requires a policy condition")
    elif kind == "semantic-mapping":
        required = {"mapping_version", "source", "target_type", "fields", "lineage_required", "preserve_raw_reference", "change_approval"}
        _require_fields(content, required, asset["id"])
        if content.get("mapping_version") != asset["version"] or any(not isinstance(content.get(field), str) or not content[field].strip() for field in ("source", "target_type")):
            raise FoundationError(f"{asset['id']} requires concrete source, target_type, and matching mapping_version")
        if not isinstance(content["fields"], dict) or not content["fields"] or any(not isinstance(key, str) or not isinstance(value, str) or not key.strip() or not value.strip() for key, value in content["fields"].items()) or content["lineage_required"] is not True or content["preserve_raw_reference"] is not True:
            raise FoundationError(f"{asset['id']} requires versioned fields, lineage, and raw reference")
        if not isinstance(content["change_approval"], dict) or not content["change_approval"].get("gate") or content["change_approval"].get("decision_required") is not True:
            raise FoundationError(f"{asset['id']} requires change approval")
    elif kind == "knowledge":
        entries = content.get("entries")
        if not isinstance(entries, list) or not entries:
            raise FoundationError(f"{asset['id']} requires knowledge entries")
        for entry in entries:
            if not isinstance(entry, dict):
                raise FoundationError(f"{asset['id']} knowledge entry must be an object")
            _require_fields(entry, {"id", "type", "status", "owner", "body_path", "classification", "scope", "provenance", "review", "effective_from", "expires_at"}, f"{asset['id']} entry")
            for field in ("id", "type", "owner", "classification", "scope"):
                if not isinstance(entry[field], str) or not entry[field].strip():
                    raise FoundationError(
                        f"{asset['id']} entry {field} must be a non-empty string"
                    )
            if not isinstance(entry.get("status"), str) or entry.get(
                "status"
            ) not in ALLOWED_STATUSES:
                raise FoundationError(f"{asset['id']} entry has an invalid status")
            _validate_provenance_record(entry["provenance"], f"{asset['id']} entry provenance")
            effective_from = _parse_timestamp(entry["effective_from"], f"{asset['id']} entry effective_from")
            expires_at = _parse_timestamp(entry["expires_at"], f"{asset['id']} entry expires_at")
            if effective_from >= expires_at:
                raise FoundationError(f"{asset['id']} entry effective_from must precede expires_at")
            if not isinstance(entry["review"], dict) or not entry["review"].get("reviewed_by") or entry["review"].get("duplicate_checked") is not True or not entry["review"].get("evidence_ref"):
                raise FoundationError(f"{asset['id']} entry requires provenance-backed duplicate review")
            if not isinstance(entry["body_path"], str):
                raise FoundationError(f"{asset['id']} has a missing knowledge body")
            body_path = _safe_path(root, entry["body_path"])
            try:
                read_control_file(
                    body_path,
                    root=root,
                    label=f"{asset['id']} knowledge body",
                )
            except FileNotFoundError as exc:
                raise FoundationError(
                    f"{asset['id']} has a missing knowledge body"
                ) from exc
            except (OSError, UnsafeControlFile) as exc:
                raise FoundationError(
                    f"{asset['id']} has an unsafe knowledge body: {exc}"
                ) from exc
    elif kind == "evaluation":
        if content.get("visibility") != "evaluation-only":
            raise FoundationError(f"{asset['id']} must be evaluation-only")
        if asset["id"] != "routing-evaluation" and (
            not isinstance(content.get("evaluator"), str)
            or content.get("evaluator") not in EVALUATOR_TYPES
        ):
            raise FoundationError(f"{asset['id']} requires a supported evaluator")
        cases = content.get("cases")
        if not isinstance(cases, list) or not cases:
            raise FoundationError(f"{asset['id']} requires evaluation cases")
        ids: set[str] = set()
        for case in cases:
            if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not isinstance(case.get("input"), dict) or "expected" not in case:
                raise FoundationError(f"{asset['id']} has an invalid evaluation case")
            if asset["id"] != "routing-evaluation" and (
                not isinstance(case["expected"], str)
                or case["expected"] not in {"pass", "fail"}
            ):
                raise FoundationError(f"{asset['id']} evaluation expected must be pass or fail")
            if case["id"] in ids:
                raise FoundationError(f"{asset['id']} has duplicate evaluation case: {case['id']}")
            ids.add(case["id"])
    elif kind.endswith("-contract"):
        _require_fields(content, {"contract_version", "references", "rules"}, asset["id"])
        if (
            not isinstance(content["references"], list)
            or any(
                not isinstance(reference, str) or not reference.strip()
                for reference in content["references"]
            )
            or not isinstance(content["rules"], list)
            or not content["rules"]
        ):
            raise FoundationError(f"{asset['id']} requires references and rules")
        for rule in content["rules"]:
            if (
                not isinstance(rule, dict)
                or not isinstance(rule.get("id"), str)
                or not isinstance(rule.get("level"), str)
                or rule.get("level") not in {"MUST", "SHOULD", "MAY"}
                or not isinstance(rule.get("condition"), dict)
            ):
                raise FoundationError(f"{asset['id']} contract rules require id, level, and condition")
            _validate_rule_metadata(rule, f"{asset['id']} rule {rule.get('id', '<unknown>')}")
            _validate_condition(rule["condition"], rule["id"])
        if asset["id"] == "human-gate-contract":
            gates = content.get("gates")
            if not isinstance(gates, list) or not gates or any(
                not isinstance(gate, dict)
                or not isinstance(gate.get("id"), str)
                or not gate["id"].strip()
                for gate in gates
            ):
                raise FoundationError(
                    "human-gate-contract requires gates with non-empty ids"
                )


def _validate_knowledge_promotion_contracts(
    assets: dict[str, dict[str, Any]],
) -> None:
    entries: dict[str, dict[str, Any]] = {}
    for asset in assets.values():
        if asset.get("kind") != "knowledge":
            continue
        for entry in asset.get("content", {}).get("entries", []):
            entry_id = entry["id"]
            if entry_id in entries:
                raise FoundationError(f"duplicate knowledge entry id: {entry_id}")
            entries[entry_id] = entry

    conditions: dict[str, list[dict[str, Any]]] = {}
    for asset in assets.values():
        for rule in asset.get("content", {}).get("rules", []):
            condition = rule.get("condition") if isinstance(rule, dict) else None
            if (
                isinstance(condition, dict)
                and condition.get("type") == "required-promotion-review"
            ):
                conditions.setdefault(condition["entry_ref"], []).append(condition)

    unknown = sorted(set(conditions) - set(entries))
    if unknown:
        raise FoundationError(
            "Knowledge promotion condition references unknown entries: "
            + ", ".join(unknown)
        )
    for entry_id, entry in entries.items():
        matching = conditions.get(entry_id, [])
        if len(matching) != 1:
            raise FoundationError(
                f"Knowledge entry {entry_id} requires exactly one promotion-review condition"
            )
        condition = matching[0]
        review = entry["review"]
        if review.get("reviewed_by") != condition["reviewed_by"]:
            raise FoundationError(
                f"Knowledge entry {entry_id} reviewer does not match its promotion contract"
            )
        if review.get("evidence_ref") not in condition["evidence_refs"]:
            raise FoundationError(
                f"Knowledge entry {entry_id} evidence_ref does not match its promotion contract"
            )
        for field in ("effective_from", "expires_at"):
            if entry[field] != condition[field]:
                raise FoundationError(
                    f"Knowledge entry {entry_id} {field} does not match its promotion contract"
                )


def _validate_cross_references(assets: dict[str, dict[str, Any]]) -> None:
    for asset in assets.values():
        references = asset.get("extends", [])
        if not isinstance(references, list):
            raise FoundationError(f"{asset['id']} extends must be a list")
        for reference in references:
            parent_id = _reference_id(reference, f"{asset['id']} extends")
            parent = assets.get(parent_id)
            if parent is None:
                raise FoundationError(f"{asset['id']} references unknown asset: {parent_id}")
            if reference["version"] != parent["version"]:
                raise FoundationError(f"{asset['id']} extends {parent_id} with an unmatching version")
        content_refs = asset.get("content", {}).get("references", [])
        if not isinstance(content_refs, list):
            raise FoundationError(f"{asset['id']} content references must be a list")
        for reference in content_refs:
            if not isinstance(reference, str) or not reference.strip():
                raise FoundationError(
                    f"{asset['id']} content references must contain asset ids"
                )
            if reference not in assets:
                raise FoundationError(f"{asset['id']} references unknown contract asset: {reference}")
        rules = asset.get("content", {}).get("rules", [])
        for rule in rules if isinstance(rules, list) else []:
            condition = rule.get("condition") if isinstance(rule, dict) else None
            if isinstance(condition, dict) and condition.get("type") == "extension-cannot-weaken-must":
                parent_asset = condition.get("parent_asset")
                if parent_asset not in assets:
                    raise FoundationError(f"{asset['id']} condition references unknown parent asset: {parent_asset}")
                parent_rules = assets[parent_asset].get("content", {}).get("rules", [])
                if parent_asset != "core-model" and not any(isinstance(parent, dict) and parent.get("id") == condition.get("parent_rule_id") for parent in parent_rules):
                    raise FoundationError(f"{asset['id']} condition references unknown parent rule: {condition.get('parent_rule_id')}")
    gate_matrix = assets.get("gate-matrix")
    human_gate = assets.get("human-gate-contract")
    if gate_matrix is None or human_gate is None:
        raise FoundationError("Foundation requires a versioned gate-matrix and human-gate-contract")
    matrix_ids = {gate["id"] for gate in gate_matrix["content"]["gates"]}
    human_gates = human_gate["content"].get("gates")
    if not isinstance(human_gates, list) or any(
        not isinstance(gate, dict) or not isinstance(gate.get("id"), str)
        for gate in human_gates
    ):
        raise FoundationError("human-gate-contract has invalid gates")
    declared_gates = {gate["id"] for gate in human_gates}
    if matrix_ids != declared_gates:
        raise FoundationError("human-gate-contract gates must match gate-matrix")
    _validate_knowledge_promotion_contracts(assets)


def _load_foundation_documents(
    foundation_root: Path,
    manifest: dict[str, Any],
    *,
    document_loader: Callable[[Path], dict[str, Any]] | None = None,
) -> FoundationRelease:
    _require_fields(manifest, {"id", "kind", "version", "status", "owner", "artifacts"}, "release")
    if manifest["kind"] != "foundation-release":
        raise FoundationError("release kind must be foundation-release")
    if not isinstance(manifest["status"], str) or manifest[
        "status"
    ] not in ALLOWED_STATUSES:
        raise FoundationError("release has an invalid status")
    if not isinstance(manifest["id"], str) or not manifest["id"].strip():
        raise FoundationError("release id must be a non-empty string")
    _validate_provenance(manifest)
    descriptors = manifest["artifacts"]
    if not isinstance(descriptors, list) or not descriptors:
        raise FoundationError("release requires at least one artifact")
    assets: dict[str, dict[str, Any]] = {}
    paths: dict[Path, str] = {}
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            raise FoundationError("artifact descriptor must be an object")
        _require_fields(descriptor, {"id", "kind", "version", "path"}, "artifact")
        asset_id = descriptor["id"]
        if not isinstance(asset_id, str) or not asset_id:
            raise FoundationError("artifact id must be a non-empty string")
        if not isinstance(descriptor["kind"], str) or descriptor[
            "kind"
        ] not in ALLOWED_ASSET_KINDS:
            raise FoundationError(f"{asset_id} has an unknown asset kind: {descriptor['kind']}")
        if asset_id in assets:
            raise FoundationError(f"duplicate artifact id: {asset_id}")
        if not isinstance(descriptor["path"], str) or not descriptor["path"].strip():
            raise FoundationError(f"{asset_id} artifact path must be a non-empty string")
        asset_path = _safe_path(foundation_root, descriptor["path"])
        if asset_path == foundation_root / "release.json":
            raise FoundationError(f"{asset_id} artifact path collides with release.json")
        previous = paths.get(asset_path)
        if previous is not None:
            raise FoundationError(
                f"duplicate artifact path: {descriptor['path']} (also used by {previous})"
            )
        paths[asset_path] = asset_id
        asset = (
            document_loader(asset_path)
            if document_loader is not None
            else _load_json(asset_path, root=foundation_root)
        )
        _require_fields(asset, REQUIRED_ASSET_FIELDS, str(asset_id))
        for key in ("id", "kind", "version"):
            if asset[key] != descriptor[key]:
                raise FoundationError(f"{asset_id} descriptor mismatch: {key}")
        if not isinstance(asset["status"], str) or asset[
            "status"
        ] not in ALLOWED_STATUSES:
            raise FoundationError(f"{asset_id} has an invalid status")
        _validate_provenance(asset)
        _validate_asset_specific(foundation_root, asset)
        assets[asset_id] = asset
    _validate_cross_references(assets)
    return FoundationRelease(foundation_root, manifest, assets)


def load_foundation(root: str | Path) -> FoundationRelease:
    foundation_root = Path(root).resolve()
    return _load_foundation_documents(
        foundation_root,
        _load_json(foundation_root / "release.json", root=foundation_root),
    )


def validate_context(context: dict[str, Any]) -> bool:
    required = {"project_id", "route", "rules", "profiles", "extensions"}
    return (
        isinstance(context, dict)
        and required <= context.keys()
        and isinstance(context["route"], str)
        and context["route"] in {"query", "quick-change", "unit"}
    )


# Typed internal Foundation validation and persistence contract.
load_foundation_documents = _load_foundation_documents
load_json = _load_json
optional_json = _optional_json
parse_timestamp = _parse_timestamp
safe_path = _safe_path
validate_asset_specific = _validate_asset_specific
validate_condition = _validate_condition
validate_cross_references = _validate_cross_references
validate_provenance = _validate_provenance
validate_provenance_record = _validate_provenance_record
validate_rule_metadata = _validate_rule_metadata
write_json = _write_json
