from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .foundation import (
    FoundationError,
    FoundationRelease,
    load_foundation,
    validate_asset_provenance,
    validate_condition_definition,
    validate_rule_definition,
)
from .jsonio import write_json_atomic
from .locking import LockUnavailable, file_lock


class WorkRoute(str, Enum):
    QUERY = "query"
    QUICK_CHANGE = "quick-change"
    UNIT = "unit"


ALLOWED_AGENT_LEVELS = {"L0", "L1"}
AGENT_ALLOWED_ACTIONS = {"read", "edit", "test"}
AGENT_PROHIBITED_ACTIONS = {
    "remote",
    "deploy",
    "credential-access",
    "promote",
    "decision",
}


@dataclass(frozen=True)
class RouteRequest:
    change: str
    risk: str
    ambiguous: bool = False
    multi_party: bool = False
    remote: bool = False
    sensitive: bool = False


@dataclass(frozen=True)
class RouteDecision:
    route: WorkRoute
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"route": self.route.value, "reasons": list(self.reasons)}


def classify_work(request: RouteRequest) -> RouteDecision:
    if request.change not in {"none", "local", "persistent"}:
        raise ValueError("change must be one of: none, local, persistent")
    if request.risk not in {"low", "high"}:
        raise ValueError("risk must be one of: low, high")
    reasons: list[str] = []
    if request.change == "persistent":
        reasons.append("persistent change")
    if request.risk == "high":
        reasons.append("high risk")
    if request.ambiguous:
        reasons.append("ambiguous acceptance criteria")
    if request.multi_party:
        reasons.append("multi-party decision")
    if request.remote:
        reasons.append("remote side effect")
    if request.sensitive:
        reasons.append("sensitive data or credentials")
    if reasons:
        return RouteDecision(WorkRoute.UNIT, tuple(reasons))
    if request.change == "none":
        return RouteDecision(WorkRoute.QUERY, ("no persistent change",))
    return RouteDecision(WorkRoute.QUICK_CHANGE, ("small reversible local change",))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FoundationError(f"missing project manifest: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FoundationError(f"invalid project manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise FoundationError("project manifest must be a JSON object")
    return value


def _load_project_extension(
    project_root: Path,
    entry: Any,
    foundation: FoundationRelease,
) -> dict[str, Any]:
    if isinstance(entry, str):
        asset = foundation.assets.get(entry)
        if asset is None or asset["kind"] != "extension":
            raise FoundationError(f"project references invalid extension: {entry}")
        return asset
    if not isinstance(entry, dict):
        raise FoundationError("project extension reference must be an ID or object")
    asset_id = entry.get("id")
    relative_path = entry.get("path")
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise FoundationError("project extension reference needs id")
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise FoundationError(f"project extension {asset_id} needs path")
    extension_path = (project_root / relative_path).resolve()
    try:
        extension_path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise FoundationError(f"project extension path escapes project root: {relative_path}") from exc
    asset = _load_json(extension_path)
    required = {
        "id", "kind", "version", "schema_version", "status", "owner", "provenance",
        "classification", "scope", "content",
    }
    missing = sorted(required - asset.keys())
    if missing:
        raise FoundationError(
            f"project extension {asset_id} missing fields: {', '.join(missing)}"
        )
    validate_asset_provenance(asset)
    if asset["id"] != asset_id:
        raise FoundationError(f"project extension descriptor mismatch: {asset_id}")
    if asset["kind"] != "extension":
        raise FoundationError(f"project asset {asset_id} kind must be extension")
    if asset["status"] not in {"draft", "approved", "deprecated"}:
        raise FoundationError(f"project extension {asset_id} has an invalid status")
    content = asset["content"]
    if not isinstance(content, dict) or not isinstance(content.get("namespace"), str) or not content["namespace"].strip():
        raise FoundationError(f"project extension {asset_id} requires a namespace")
    extends = asset.get("extends", [])
    extension_rules = content.get("rules", [])
    if not isinstance(extension_rules, list):
        raise FoundationError(f"project extension {asset_id} rules must be a list")
    level_strength = {"MAY": 0, "SHOULD": 1, "MUST": 2}
    for rule in extension_rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or rule.get("level") not in level_strength:
            raise FoundationError(f"project extension {asset_id} has an invalid rule")
        validate_rule_definition(rule, f"{asset_id} rule {rule.get('id', '<unknown>')}")
        condition = rule.get("condition")
        if not isinstance(condition, dict) or condition.get("type") != "extension-cannot-weaken-must":
            raise FoundationError(f"project extension {asset_id} rules require extension integrity condition")
        validate_condition_definition(condition, rule.get("id", "extension-rule"))
        parent_asset = foundation.assets.get(condition.get("parent_asset"))
        if parent_asset is None or condition.get("parent_level") != "MUST":
            raise FoundationError(f"project extension {asset_id} has an invalid parent MUST reference")
        parent_rules = parent_asset.get("content", {}).get("rules", [])
        parent_rule = next((item for item in parent_rules if isinstance(item, dict) and item.get("id") == condition.get("parent_rule_id")), None)
        if parent_rule is None or level_strength[rule["level"]] < level_strength["MUST"]:
            raise FoundationError(f"project extension {asset_id} weakens an inherited MUST rule")
    if not isinstance(extends, list):
        raise FoundationError(f"project extension {asset_id} extends must be a list")
    for parent in extends:
        if not isinstance(parent, dict):
            raise FoundationError(
                f"project extension {asset_id} extends must pin parent versions"
            )
        parent_id = parent.get("id")
        parent_version = parent.get("version")
        if not isinstance(parent_id, str) or not isinstance(parent_version, str):
            raise FoundationError(
                f"project extension {asset_id} has an invalid pinned parent"
            )
        foundation_parent = foundation.assets.get(parent_id)
        if foundation_parent is None:
            raise FoundationError(
                f"project extension {asset_id} references unknown Foundation asset: {parent_id}"
            )
        if parent_version != foundation_parent["version"]:
            raise FoundationError(
                f"project extension {asset_id} parent version mismatch: {parent_id}"
            )
    asset["source_path"] = str(extension_path)
    return asset


def load_project(
    path: str | Path,
) -> tuple[Path, dict[str, Any], FoundationRelease, list[dict[str, Any]]]:
    requested_path = Path(path).expanduser()
    if requested_path.is_dir():
        # Import lazily because session owns discovery and imports workflow types.
        from .session import discover_project

        manifest_path = discover_project(requested_path)
    else:
        manifest_path = requested_path.resolve()
    project = _load_json(manifest_path)
    required = {"id", "kind", "version", "foundation_path", "profiles", "extensions"}
    missing = sorted(required - project.keys())
    if missing:
        raise FoundationError(f"project manifest missing fields: {', '.join(missing)}")
    if project["kind"] != "project":
        raise FoundationError("project manifest kind must be project")
    schema_version = str(project.get("schema_version", "1.0.0"))
    if schema_version != "1.0.0":
        raise FoundationError("project manifest has an unsupported schema_version")
    foundation = load_foundation(manifest_path.parent / str(project["foundation_path"]))

    lock_path = manifest_path.parent / "isekai.lock.json"
    if lock_path.is_file():
        from .distribution import load_install_lock, tree_digest

        lock = load_install_lock(manifest_path.parent)
        foundation_pin = lock.get("foundation") if lock else None
        if not isinstance(foundation_pin, dict):
            raise FoundationError("isekai.lock.json has no Foundation pin")
        if foundation.version != foundation_pin.get("version"):
            raise FoundationError("Project Foundation version does not match isekai.lock.json")
        if tree_digest(foundation.root) != foundation_pin.get("digest"):
            raise FoundationError("Project Foundation digest does not match isekai.lock.json")

    profiles = project["profiles"]
    if not isinstance(profiles, list):
        raise FoundationError("project profiles must be a list")
    document_language = str(project.get("document_language", "ko"))
    if document_language not in {"ko", "en"}:
        raise FoundationError("project document_language must be ko or en")
    maximum_agent_level = str(project.get("maximum_agent_level", "L0"))
    if maximum_agent_level not in ALLOWED_AGENT_LEVELS:
        raise FoundationError(
            "project maximum_agent_level must be one of: "
            + ", ".join(sorted(ALLOWED_AGENT_LEVELS))
        )
    for asset_id in profiles:
        asset = foundation.assets.get(asset_id)
        if asset is None or asset["kind"] != "profile":
            raise FoundationError(f"project references invalid profile: {asset_id}")

    raw_extensions = project["extensions"]
    if not isinstance(raw_extensions, list):
        raise FoundationError("project extensions must be a list")
    project_extensions = [
        _load_project_extension(manifest_path.parent, entry, foundation)
        for entry in raw_extensions
    ]
    normalized_project = dict(project)
    normalized_project["schema_version"] = schema_version
    normalized_project["profiles"] = list(profiles)
    normalized_project["document_language"] = document_language
    normalized_project["maximum_agent_level"] = maximum_agent_level
    normalized_project["extensions"] = [asset["id"] for asset in project_extensions]
    return manifest_path, normalized_project, foundation, project_extensions


def resolve_context(path: str | Path, route: WorkRoute = WorkRoute.UNIT) -> dict[str, Any]:
    manifest_path, project, foundation, project_extensions = load_project(path)
    applicable_rules: list[dict[str, Any]] = []
    for rule in foundation.rules():
        targets = rule.get("applies_to", [])
        if "*" in targets or route.value in targets:
            applicable_rules.append(dict(rule))

    body = {
        "project_id": project["id"],
        "project_version": project["version"],
        "project_schema_version": project["schema_version"],
        "document_language": project["document_language"],
        "foundation_id": foundation.manifest["id"],
        "foundation_version": foundation.version,
        "foundation_digest": foundation.contract_digest,
        "profiles": project["profiles"],
        "extensions": project["extensions"],
        "extension_assets": sorted(project_extensions, key=lambda asset: asset["id"]),
        "route": route.value,
        "maximum_agent_level": project.get("maximum_agent_level", "L0"),
        "rule_ids": sorted(rule["id"] for rule in applicable_rules),
        "rules": sorted(applicable_rules, key=lambda rule: rule["id"]),
        "policy_ids": sorted(foundation.assets_by_kind("policy"), key=lambda item: item["id"]),
        "source_manifest": str(manifest_path),
    }
    body["policy_ids"] = [item["id"] for item in body["policy_ids"]]
    digest_input = json.dumps(body, sort_keys=True, separators=(",", ":"))
    receipt = {
        "receipt_id": "CTX-" + hashlib.sha256(digest_input.encode()).hexdigest()[:16],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **body,
    }
    return receipt


def _slugify(value: str) -> str:
    slug = re.sub(r"[^\w]+", "-", value.lower(), flags=re.UNICODE).strip("-")
    if not slug:
        raise ValueError("title must contain at least one letter or number")
    return slug[:48]


def _write_json(path: Path, value: Any) -> None:
    write_json_atomic(path, value)


def initialize_project(
    path: str | Path = ".",
    *,
    project_id: str | None = None,
    foundation_path: str | None = None,
    profiles: list[str] | None = None,
    document_language: str = "ko",
    maximum_agent_level: str = "L0",
) -> Path:
    project_root = Path(path).expanduser().resolve()
    if not project_root.is_dir():
        raise ValueError(f"project root does not exist or is not a directory: {project_root}")

    manifest_path = project_root / "project.json"
    if manifest_path.exists():
        raise FileExistsError(f"project manifest already exists: {manifest_path}")

    resolved_id = str(project_id or project_root.name).strip()
    if not resolved_id:
        raise ValueError("project id must be a non-empty string")
    if foundation_path is None:
        from .distribution import load_install_lock

        lock = load_install_lock(project_root)
        pinned_path = lock.get("foundation", {}).get("path") if lock else None
        foundation_path = str(pinned_path or "foundation")
    if not isinstance(foundation_path, str) or not foundation_path.strip():
        raise ValueError("foundation_path must be a non-empty string")
    if document_language not in {"ko", "en"}:
        raise ValueError("document_language must be ko or en")
    if maximum_agent_level not in ALLOWED_AGENT_LEVELS:
        raise ValueError(
            "maximum_agent_level must be one of: "
            + ", ".join(sorted(ALLOWED_AGENT_LEVELS))
        )

    selected_profiles = list(profiles or [])
    if any(not isinstance(item, str) or not item.strip() for item in selected_profiles):
        raise ValueError("profiles must contain non-empty strings")
    foundation = load_foundation(project_root / foundation_path)
    for profile_id in selected_profiles:
        asset = foundation.assets.get(profile_id)
        if asset is None or asset.get("kind") != "profile":
            raise FoundationError(f"project references invalid profile: {profile_id}")

    manifest = {
        "id": resolved_id,
        "kind": "project",
        "schema_version": "1.0.0",
        "version": "0.1.0",
        "foundation_path": foundation_path,
        "profiles": selected_profiles,
        "extensions": [],
        "document_language": document_language,
        "maximum_agent_level": maximum_agent_level,
    }
    units_root = project_root / "units"
    created_units_root = not units_root.exists()
    units_root.mkdir(parents=False, exist_ok=True)
    try:
        with manifest_path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        load_project(manifest_path)
    except Exception:
        manifest_path.unlink(missing_ok=True)
        if created_units_root:
            try:
                units_root.rmdir()
            except OSError:
                pass
        raise
    return manifest_path


def initialize_unit(
    project_path: str | Path,
    title: str,
    output_root: str | Path | None = None,
    owner: str = "unassigned",
    intent: dict[str, Any] | None = None,
) -> Path:
    receipt = resolve_context(project_path, WorkRoute.UNIT)
    manifest_path = Path(str(receipt["source_manifest"])).resolve()
    project_root = manifest_path.parent.resolve()
    if output_root is None:
        resolved_output_root = project_root / "units"
    else:
        requested_output_root = Path(output_root).expanduser()
        if requested_output_root.is_absolute():
            resolved_output_root = requested_output_root.resolve()
        else:
            resolved_output_root = (project_root / requested_output_root).resolve()
            try:
                resolved_output_root.relative_to(project_root)
            except ValueError as exc:
                raise ValueError(
                    f"relative Unit output escapes project root: {output_root}"
                ) from exc
    intent_values = dict(intent or {})
    goal = str(intent_values.get("goal") or title).strip()
    intent_source = str(intent_values.get("source") or "direct-request")
    expected_outcome = str(intent_values.get("expected_outcome") or "").strip()
    work_scope = list(intent_values.get("scope") or [])
    constraints = list(intent_values.get("constraints") or [])
    acceptance_criteria = list(intent_values.get("acceptance_criteria") or [])
    document_language = receipt["document_language"]
    slug = _slugify(title)
    unit_id = f"UNIT-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{slug.upper()}"
    unit_dir = resolved_output_root.resolve() / unit_id.lower()
    if unit_dir.exists():
        raise FileExistsError(f"unit already exists: {unit_dir}")
    unit_dir.mkdir(parents=True)

    _write_json(
        unit_dir / "unit.json",
        {
            "id": unit_id,
            "title": title,
            "project_id": receipt["project_id"],
            "phase": "inception",
            "status": "proposed",
            "owner": owner,
            "scope": f"project:{receipt['project_id']}",
            "work_scope": work_scope,
            "intent_source": intent_source,
            "document_language": document_language,
            "goal": goal,
            "expected_outcome": expected_outcome,
            "constraints": constraints,
            "acceptance_criteria": acceptance_criteria,
            "foundation_version": receipt["foundation_version"],
            "foundation_digest": receipt["foundation_digest"],
        },
    )
    if document_language == "ko":
        intent_heading = {
            "goal": "## 목표",
            "outcome": "## 기대 결과",
            "scope": "## 범위",
            "constraints": "## 제약사항",
            "acceptance": "## 인수 조건",
        }
        expected_placeholder = "기대 결과를 정의합니다."
        scope_placeholder = "- 작업 범위를 정의합니다."
        constraint_placeholder = "- 제약사항을 정의합니다."
        acceptance_placeholder = "- [ ] 검증 가능한 인수 조건을 정의합니다."
        templates = {
            "requirements.md": "# 요구사항\n\n요구사항과 명시적 비목표를 기록합니다.\n",
            "architecture.md": "# 아키텍처\n\n승인된 아키텍처와 외부 계약을 기록합니다.\n",
            "plan.md": "# 계획\n\n구현·검증 계획을 기록합니다.\n",
            "acceptance.md": "# 인수 조건\n\n- [ ] 검증 가능한 인수 조건을 정의합니다.\n",
            "release.md": "# 릴리스\n\n릴리스 결정·근거·Rollback 계획을 기록합니다.\n",
            "operations.md": "# 운영\n\n배포 결과·운영 피드백·후속 작업을 기록합니다.\n",
            "implementation-guide.md": "# 구현 가이드\n\n이 문서는 코드의 동작 방식·작성 표준·사용 예제·한계를 설명합니다. 시스템 구조와 설계 선택의 근거는 architecture.md에 기록합니다.\n\n## 코드 표준\n\n- 사용하는 언어·프레임워크·테스트 표준을 기록합니다.\n\n## 동작 흐름\n\n- 주요 등록·조회·변경 흐름을 단계별로 설명합니다.\n\n## 오류와 검증\n\n- 실패 조건과 검증 방법을 기록합니다.\n\n## 사용 예제\n\n- 실제 호출·사용 예제를 기록합니다.\n\n## 한계와 확장\n\n- 현재 구현의 한계와 다음 확장 방향을 기록합니다.\n",
        }
        pending = ["Inception 내용 구체화"]
        next_action = "의도와 인수 조건을 구체화합니다."
    else:
        intent_heading = {
            "goal": "## Goal",
            "outcome": "## Expected outcome",
            "scope": "## Scope",
            "constraints": "## Constraints",
            "acceptance": "## Acceptance criteria",
        }
        expected_placeholder = "Define the expected outcome."
        scope_placeholder = "- Define the work scope."
        constraint_placeholder = "- Define constraints."
        acceptance_placeholder = "- [ ] Define verifiable acceptance criteria."
        templates = {
            "requirements.md": "# Requirements\n\nDocument the requirements and explicit non-goals.\n",
            "architecture.md": "# Architecture\n\nDocument the approved architecture and external contracts.\n",
            "plan.md": "# Plan\n\nDocument the construction and validation plan.\n",
            "acceptance.md": "# Acceptance Criteria\n\n- [ ] Define verifiable acceptance criteria.\n",
            "release.md": "# Release\n\nRecord the release decision, evidence, and rollback plan.\n",
            "operations.md": "# Operations\n\nRecord deployment, operational feedback, and follow-up work.\n",
            "implementation-guide.md": "# Implementation Guide\n\nExplain code behavior, coding conventions, usage examples, and limitations. Record system structure and design rationale in architecture.md.\n\n## Coding standard\n\n- Record the language, framework, and test standards.\n\n## Behavior flow\n\n- Explain the main registration, lookup, and change flows.\n\n## Errors and verification\n\n- Record failure conditions and verification methods.\n\n## Usage example\n\n- Record practical invocation and usage examples.\n\n## Limitations and extensions\n\n- Record current limitations and next extension directions.\n",
        }
        pending = ["inception elaboration"]
        next_action = "clarify intent and acceptance criteria"

    scope_lines = [f"- {item}" for item in work_scope] or [scope_placeholder]
    constraint_lines = [f"- {item}" for item in constraints] or [constraint_placeholder]
    acceptance_lines = [f"- [ ] {item}" for item in acceptance_criteria] or [acceptance_placeholder]
    intent_lines = [
        f"# {title}",
        "",
        intent_heading["goal"],
        "",
        goal,
        "",
        intent_heading["outcome"],
        "",
        expected_outcome or expected_placeholder,
        "",
        intent_heading["scope"],
        "",
        *scope_lines,
        "",
        intent_heading["constraints"],
        "",
        *constraint_lines,
        "",
        intent_heading["acceptance"],
        "",
        *acceptance_lines,
        "",
    ]
    (unit_dir / "intent.md").write_text("\n".join(intent_lines), encoding="utf-8")
    for relative, content in templates.items():
        (unit_dir / relative).write_text(content, encoding="utf-8")
    (unit_dir / "evaluations").mkdir()
    (unit_dir / "evidence").mkdir()
    _write_json(
        unit_dir / "evaluations/criteria.json",
        {
            "unit_id": unit_id,
            "visibility": "evaluation-only",
            "criteria": [],
        },
    )
    _write_json(
        unit_dir / "evidence/verification.json",
        {
            "id": "",
            "type": "verification-evidence",
            "schema_version": "1.0.0",
            "unit_id": unit_id,
            "passed": False,
            "scope": "",
            "recorded_by": "",
            "recorded_at": "",
            "commands": [],
        },
    )
    _write_json(unit_dir / "decisions.json", {"unit_id": unit_id, "decisions": []})
    envelope_now = datetime.now(timezone.utc)
    initial_envelope = {
        "id": f"ENV-{unit_id}-INITIAL",
        "type": "execution-envelope",
        "schema_version": "1.0.0",
        "unit_id": unit_id,
        "status": "proposed",
        "scope": [],
        "stages": [],
        "allowed_actions": [],
        "forbidden_actions": sorted(AGENT_PROHIBITED_ACTIONS),
        "max_iterations": 0,
        "proposed_by": owner,
        "proposed_at": envelope_now.isoformat(),
        "expires_at": (
            envelope_now + timedelta(hours=EXECUTION_ENVELOPE_DEFAULT_HOURS)
        ).isoformat(),
    }
    initial_envelope["approval_digest"] = _execution_envelope_approval_digest(
        initial_envelope
    )
    _write_json(unit_dir / "execution-envelope.json", initial_envelope)
    _write_json(
        unit_dir / "execution-authorizations.json",
        {
            "type": "execution-authorization-ledger",
            "schema_version": "1.0.0",
            "unit_id": unit_id,
            "envelope_id": initial_envelope["id"],
            "approval_digest": initial_envelope["approval_digest"],
            "grants": [],
        },
    )
    _write_json(
        unit_dir / "checkpoint.json",
        {
            "unit_id": unit_id,
            "completed": [],
            "pending": pending,
            "blocked_by": [],
            "next_action": next_action,
        },
    )
    _write_json(unit_dir / "context-receipt.json", receipt)
    return unit_dir


PROTECTED_UNIT_ARTIFACTS = {
    "unit.json",
    "context-receipt.json",
    "decisions.json",
    "execution-envelope.json",
    "execution-authorizations.json",
}
UNIT_LOCK_NAME = ".isekai-unit.lock"


@contextmanager
def unit_lock(unit_dir: Path):
    """Serialize every mutation of one Unit.

    ``decisions.json`` and ``unit.json`` are read-modify-write ledgers. Without a
    shared lock two agents working the same Unit overwrite each other's records,
    and the loser's postflight cannot tell, because it only sees that its own
    record landed.
    """
    with file_lock(unit_dir / UNIT_LOCK_NAME, subject=f"Unit {unit_dir.name}"):
        yield


UNIT_REQUIRED_FILES = {
    "unit.json",
    "intent.md",
    "requirements.md",
    "decisions.json",
    "architecture.md",
    "plan.md",
    "acceptance.md",
    "evaluations/criteria.json",
    "evidence/verification.json",
    "release.md",
    "operations.md",
    "implementation-guide.md",
    "checkpoint.json",
    "context-receipt.json",
    "execution-envelope.json",
    "execution-authorizations.json",
}


def _unit_json(unit_dir: Path, relative: str) -> dict[str, Any]:
    path = unit_dir / relative
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing Unit file: {relative}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Unit JSON in {relative}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Unit JSON must be an object: {relative}")
    return value


def _unit_preflight_issues(unit_dir: Path) -> list[str]:
    issues: list[str] = []
    try:
        unit = _unit_json(unit_dir, "unit.json")
    except ValueError as exc:
        return [str(exc)]
    scope = unit.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        issues.append("Unit scope is missing or ambiguous")
    try:
        receipt = _unit_json(unit_dir, "context-receipt.json")
    except ValueError as exc:
        return issues + [str(exc)]
    required_context = {
        "project_id",
        "route",
        "rules",
        "profiles",
        "extensions",
        "foundation_version",
        "foundation_digest",
        "source_manifest",
    }
    missing_context = sorted(required_context - receipt.keys())
    if missing_context:
        issues.append(
            f"Context Receipt missing fields: {', '.join(missing_context)}"
        )
    if receipt.get("project_id") != unit.get("project_id"):
        issues.append("Context Receipt project_id does not match Unit")
    if receipt.get("route") != WorkRoute.UNIT.value:
        issues.append("Context Receipt route must be unit")
    if receipt.get("foundation_version") != unit.get("foundation_version"):
        issues.append("Context Receipt foundation_version does not match Unit")
    if receipt.get("foundation_digest") != unit.get("foundation_digest"):
        issues.append("Context Receipt foundation_digest does not match Unit")
    rules = receipt.get("rules")
    if not isinstance(rules, list) or not rules:
        issues.append("Context Receipt has no full applied rules")
    else:
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                issues.append(f"Context rule {index} must be an object")
                continue
            if rule.get("level") == "MUST" and not isinstance(rule.get("condition"), dict):
                issues.append(f"Context MUST rule {index} has no machine condition")
    return issues


EXECUTION_ENVELOPE_REQUIRED_FIELDS = {
    "id",
    "type",
    "schema_version",
    "unit_id",
    "status",
    "scope",
    "stages",
    "allowed_actions",
    "forbidden_actions",
    "max_iterations",
    "proposed_by",
    "proposed_at",
    "expires_at",
    "approval_digest",
}
EXECUTION_ENVELOPE_STATUSES = {"proposed", "approved"}
# An Envelope bounds how long an approval keeps authorizing actions. Units are
# meant to span sessions, so the default is a working week rather than a day,
# and an expired Envelope is renewed through a fresh human Decision.
EXECUTION_ENVELOPE_DEFAULT_HOURS = 168
EXECUTION_ENVELOPE_MAX_HOURS = 720
# Statuses in which an Envelope may be proposed or re-proposed. Re-proposing
# revokes the active approval until a new Inception Decision approves it.
EXECUTION_ENVELOPE_PROPOSABLE_STATUSES = {
    "proposed",
    "inception",
    "awaiting-inception-decision",
    "construction",
    "awaiting-release-decision",
}
EXECUTION_ENVELOPE_APPROVAL_FIELDS = {
    "id",
    "type",
    "schema_version",
    "unit_id",
    "scope",
    "stages",
    "allowed_actions",
    "forbidden_actions",
    "max_iterations",
    "proposed_by",
    "proposed_at",
    "expires_at",
}


def _execution_envelope_approval_digest(envelope: dict[str, Any]) -> str:
    subject = {
        field: envelope.get(field)
        for field in sorted(EXECUTION_ENVELOPE_APPROVAL_FIELDS)
    }
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _scope_pattern_issue(pattern: str) -> str | None:
    normalized = pattern.replace("\\", "/")
    if (
        not normalized.strip()
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized)
        or ".." in normalized.split("/")
    ):
        return f"Execution Envelope scope must be a project-relative pattern: {pattern}"
    return None


def _execution_envelope_issues(
    envelope: Any,
    unit_id: str | None = None,
    *,
    require_approved: bool = False,
    check_expiry: bool = True,
) -> list[str]:
    """Report structural problems with an Envelope.

    ``check_expiry`` separates the two questions an Envelope answers. Structure
    and binding are permanent properties, so ``verify_unit`` checks them for the
    whole life of a Unit. Expiry only decides whether the approval still
    authorizes new actions, so it is checked when granting or binding an
    approval - never when auditing a Unit that has already moved on.
    """
    if not isinstance(envelope, dict):
        return ["Execution Envelope must be an object"]
    issues: list[str] = []
    missing = sorted(EXECUTION_ENVELOPE_REQUIRED_FIELDS - envelope.keys())
    if missing:
        issues.append(f"Execution Envelope missing fields: {', '.join(missing)}")
    if envelope.get("type") != "execution-envelope":
        issues.append("Execution Envelope has an invalid type")
    if envelope.get("schema_version") != "1.0.0":
        issues.append("Execution Envelope has an unsupported schema_version")
    if unit_id is not None and envelope.get("unit_id") != unit_id:
        issues.append("Execution Envelope unit_id does not match Unit")
    if envelope.get("status") not in EXECUTION_ENVELOPE_STATUSES:
        issues.append("Execution Envelope has an invalid status")
    approval_digest = envelope.get("approval_digest")
    if not isinstance(approval_digest, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", approval_digest
    ):
        issues.append("Execution Envelope approval_digest must be a SHA-256 digest")
    elif approval_digest != _execution_envelope_approval_digest(envelope):
        issues.append("Execution Envelope approval_digest does not match its approval subject")
    expires_at = envelope.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at.strip():
        issues.append("Execution Envelope requires expires_at")
    else:
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if check_expiry and expiry <= datetime.now(timezone.utc):
                issues.append(
                    "Execution Envelope is expired; propose a new Envelope and "
                    "record a new approved inception Decision to renew it"
                )
        except ValueError:
            issues.append("Execution Envelope expires_at must be an ISO-8601 timestamp")
    if require_approved and envelope.get("status") != "approved":
        issues.append("Execution Envelope is not approved")
    for field in ("scope", "allowed_actions", "forbidden_actions"):
        value = envelope.get(field)
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            issues.append(f"Execution Envelope {field} must be a list of strings")
        elif field in {"scope", "allowed_actions"} and not value:
            issues.append(f"Execution Envelope {field} must not be empty")
        if field == "scope" and isinstance(value, list):
            issues.extend(
                issue
                for item in value
                if isinstance(item, str)
                for issue in [_scope_pattern_issue(item)]
                if issue is not None
            )
    allowed_actions = envelope.get("allowed_actions")
    forbidden_actions = envelope.get("forbidden_actions")
    if isinstance(allowed_actions, list) and all(
        isinstance(item, str) for item in allowed_actions
    ):
        unknown_actions = sorted(set(allowed_actions) - AGENT_ALLOWED_ACTIONS)
        if unknown_actions:
            issues.append(
                "Execution Envelope contains unsupported allowed actions: "
                + ", ".join(unknown_actions)
            )
        prohibited_actions = sorted(set(allowed_actions) & AGENT_PROHIBITED_ACTIONS)
        if prohibited_actions:
            issues.append(
                "Execution Envelope cannot allow prohibited actions: "
                + ", ".join(prohibited_actions)
            )
        if isinstance(forbidden_actions, list) and all(
            isinstance(item, str) for item in forbidden_actions
        ):
            overlap = sorted(set(allowed_actions) & set(forbidden_actions))
            if overlap:
                issues.append(
                    "Execution Envelope actions cannot be both allowed and forbidden: "
                    + ", ".join(overlap)
                )
    max_iterations = envelope.get("max_iterations")
    if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations <= 0:
        issues.append("Execution Envelope max_iterations must be a positive integer")
    stages = envelope.get("stages")
    if not isinstance(stages, list) or not stages:
        issues.append("Execution Envelope must define at least one stage")
    else:
        seen_stage_names: set[str] = set()
        for index, stage in enumerate(stages):
            if not isinstance(stage, dict):
                issues.append(f"Execution Envelope stage {index} must be an object")
                continue
            if not isinstance(stage.get("name"), str) or not stage["name"].strip():
                issues.append(f"Execution Envelope stage {index} needs name")
            elif stage["name"] in seen_stage_names:
                issues.append(f"Execution Envelope has duplicate stage: {stage['name']}")
            else:
                seen_stage_names.add(stage["name"])
            if not isinstance(stage.get("depth"), str) or not stage["depth"].strip():
                issues.append(f"Execution Envelope stage {index} needs depth")
            actions = stage.get("allowed_actions")
            if not isinstance(actions, list) or any(
                not isinstance(item, str) or not item.strip() for item in actions
            ):
                issues.append(
                    f"Execution Envelope stage {index} allowed_actions must be a list of strings"
                )
            else:
                unknown_stage_actions = sorted(set(actions) - AGENT_ALLOWED_ACTIONS)
                if unknown_stage_actions:
                    issues.append(
                        f"Execution Envelope stage {index} contains unsupported actions: "
                        + ", ".join(unknown_stage_actions)
                    )
                if isinstance(allowed_actions, list):
                    outside_envelope = sorted(set(actions) - set(allowed_actions))
                    if outside_envelope:
                        issues.append(
                            f"Execution Envelope stage {index} actions are not allowed by the envelope: "
                            + ", ".join(outside_envelope)
                        )
    if require_approved:
        if not isinstance(envelope.get("approval_decision_id"), str) or not envelope.get("approval_decision_id", "").strip():
            issues.append("approved Execution Envelope needs approval_decision_id")
        if not isinstance(envelope.get("approved_at"), str) or not envelope.get("approved_at", "").strip():
            issues.append("approved Execution Envelope needs approved_at")
    return issues


def propose_execution_envelope(
    path: str | Path,
    *,
    scope: list[str],
    stages: list[dict[str, Any]],
    allowed_actions: list[str],
    forbidden_actions: list[str],
    max_iterations: int,
    proposed_by: str,
    expires_in_hours: int = EXECUTION_ENVELOPE_DEFAULT_HOURS,
) -> dict[str, Any]:
    """Propose an Execution Envelope, replacing any Envelope the Unit already has.

    Re-proposing during Construction is how an expired Envelope or an exhausted
    iteration budget is renewed. The replacement starts as ``proposed``, so the
    Unit holds no authorization until a new approved inception Decision binds it.
    """
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise ValueError(f"Unit directory does not exist: {unit_dir}")
    with unit_lock(unit_dir):
        return _propose_execution_envelope_locked(
            unit_dir,
            scope=scope,
            stages=stages,
            allowed_actions=allowed_actions,
            forbidden_actions=forbidden_actions,
            max_iterations=max_iterations,
            proposed_by=proposed_by,
            expires_in_hours=expires_in_hours,
        )


def _propose_execution_envelope_locked(
    unit_dir: Path,
    *,
    scope: list[str],
    stages: list[dict[str, Any]],
    allowed_actions: list[str],
    forbidden_actions: list[str],
    max_iterations: int,
    proposed_by: str,
    expires_in_hours: int,
) -> dict[str, Any]:
    unit = _unit_json(unit_dir, "unit.json")
    if unit.get("status") not in EXECUTION_ENVELOPE_PROPOSABLE_STATUSES:
        raise ValueError(
            "Execution Envelope can only be proposed before Release; current status: "
            + str(unit.get("status"))
        )
    if not isinstance(proposed_by, str) or not proposed_by.strip():
        raise ValueError("proposed_by must be a non-empty string")
    if (
        not isinstance(expires_in_hours, int)
        or isinstance(expires_in_hours, bool)
        or not 0 < expires_in_hours <= EXECUTION_ENVELOPE_MAX_HOURS
    ):
        raise ValueError(
            "expires_in_hours must be a positive integer of at most "
            f"{EXECUTION_ENVELOPE_MAX_HOURS}"
        )
    now = datetime.now(timezone.utc)
    envelope = {
        "id": f"ENV-{unit.get('id')}-{now.strftime('%Y%m%d%H%M%S%f')}",
        "type": "execution-envelope",
        "schema_version": "1.0.0",
        "unit_id": unit.get("id"),
        "status": "proposed",
        "scope": scope,
        "stages": stages,
        "allowed_actions": allowed_actions,
        "forbidden_actions": forbidden_actions,
        "max_iterations": max_iterations,
        "proposed_by": proposed_by.strip(),
        "proposed_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=expires_in_hours)).isoformat(),
    }
    envelope["approval_digest"] = _execution_envelope_approval_digest(envelope)
    issues = _execution_envelope_issues(envelope, str(unit.get("id")))
    if issues:
        raise ValueError("Execution Envelope rejected: " + "; ".join(issues))
    _write_json(unit_dir / "execution-envelope.json", envelope)
    _write_json(
        unit_dir / "execution-authorizations.json",
        {
            "type": "execution-authorization-ledger",
            "schema_version": "1.0.0",
            "unit_id": unit.get("id"),
            "envelope_id": envelope["id"],
            "approval_digest": envelope["approval_digest"],
            "grants": [],
        },
    )
    persisted = _unit_json(unit_dir, "execution-envelope.json")
    if persisted.get("id") != envelope["id"]:
        raise ValueError("Execution Envelope postflight blocked: record was not persisted")
    return {"path": str(unit_dir / "execution-envelope.json"), "envelope": envelope}


def _approve_execution_envelope(unit_dir: Path, decision: dict[str, Any]) -> None:
    envelope = _unit_json(unit_dir, "execution-envelope.json")
    unit = _unit_json(unit_dir, "unit.json")
    issues = _execution_envelope_issues(envelope, str(unit.get("id")))
    if issues:
        raise ValueError("Execution Envelope approval blocked: " + "; ".join(issues))
    decision_issues = _decision_record_issues(
        decision,
        unit_id=str(unit.get("id")),
        scope=str(unit.get("scope")),
    )
    if decision_issues:
        raise ValueError(
            "Execution Envelope approval blocked: " + "; ".join(decision_issues)
        )
    references = decision.get("references", [])
    if "execution-envelope.json" not in references:
        raise ValueError(
            "Inception Decision must reference execution-envelope.json"
        )
    approval_subject = decision.get("approval_subject")
    if not isinstance(approval_subject, dict):
        raise ValueError("Inception Decision has no bound Execution Envelope subject")
    if approval_subject.get("type") != "execution-envelope":
        raise ValueError("Inception Decision approval subject has an invalid type")
    if approval_subject.get("id") != envelope.get("id"):
        raise ValueError("Execution Envelope was replaced after the Inception Decision")
    if approval_subject.get("digest") != envelope.get("approval_digest"):
        raise ValueError("Execution Envelope changed after the Inception Decision")
    envelope["status"] = "approved"
    envelope["approval_decision_id"] = decision["id"]
    envelope["approved_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(unit_dir / "execution-envelope.json", envelope)
    persisted = _unit_json(unit_dir, "execution-envelope.json")
    if persisted.get("status") != "approved":
        raise ValueError("Execution Envelope approval postflight blocked")


def approve_execution_envelope(path: str | Path) -> dict[str, Any]:
    """Bind the Unit's current Envelope to its latest approved inception Decision.

    The initial approval happens as part of the transition into Construction.
    This is the renewal path: after a replacement Envelope is proposed and a new
    inception Decision approves it, this activates it without another transition.
    """
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise ValueError(f"Unit directory does not exist: {unit_dir}")
    with unit_lock(unit_dir):
        preflight_issues = _unit_preflight_issues(unit_dir)
        if preflight_issues:
            raise ValueError(
                "Execution Envelope approval blocked: " + "; ".join(preflight_issues)
            )
        decisions = _unit_json(unit_dir, "decisions.json")
        unit = _unit_json(unit_dir, "unit.json")
        if decisions.get("unit_id") != unit.get("id"):
            raise ValueError("decisions.json unit_id does not match Unit")
        decision = _latest_decision(decisions, "inception")
        if decision is None or decision.get("outcome") != "approved":
            raise ValueError("Execution Envelope needs an approved inception Decision")
        _approve_execution_envelope(unit_dir, decision)
        envelope = _unit_json(unit_dir, "execution-envelope.json")
    return {
        "path": str(unit_dir / "execution-envelope.json"),
        "envelope": envelope,
        "approval_decision_id": decision["id"],
    }


AUTHORIZATION_LEDGER_REQUIRED_FIELDS = {
    "type",
    "schema_version",
    "unit_id",
    "envelope_id",
    "approval_digest",
    "grants",
}
AUTHORIZATION_GRANT_REQUIRED_FIELDS = {
    "id",
    "action",
    "target",
    "stage",
    "iteration",
    "decision_id",
    "envelope_digest",
    "authorized_at",
}


def _authorization_ledger_issues(
    ledger: Any,
    unit: dict[str, Any],
    envelope: dict[str, Any],
) -> list[str]:
    if not isinstance(ledger, dict):
        return ["Execution authorization ledger must be an object"]
    issues: list[str] = []
    missing = sorted(AUTHORIZATION_LEDGER_REQUIRED_FIELDS - ledger.keys())
    if missing:
        issues.append(
            "Execution authorization ledger missing fields: " + ", ".join(missing)
        )
    if ledger.get("type") != "execution-authorization-ledger":
        issues.append("Execution authorization ledger has an invalid type")
    if ledger.get("schema_version") != "1.0.0":
        issues.append("Execution authorization ledger has an unsupported schema_version")
    if ledger.get("unit_id") != unit.get("id"):
        issues.append("Execution authorization ledger unit_id does not match Unit")
    if ledger.get("envelope_id") != envelope.get("id"):
        issues.append("Execution authorization ledger does not match the active Envelope")
    if ledger.get("approval_digest") != envelope.get("approval_digest"):
        issues.append("Execution authorization ledger digest does not match the active Envelope")
    grants = ledger.get("grants")
    if not isinstance(grants, list):
        issues.append("Execution authorization ledger grants must be a list")
        return issues
    max_iterations = envelope.get("max_iterations")
    if isinstance(max_iterations, int) and not isinstance(max_iterations, bool):
        if len(grants) > max_iterations:
            issues.append("Execution authorization ledger exceeds max_iterations")
    for index, grant in enumerate(grants):
        if not isinstance(grant, dict):
            issues.append(f"Execution authorization grant {index} must be an object")
            continue
        missing_grant = sorted(AUTHORIZATION_GRANT_REQUIRED_FIELDS - grant.keys())
        if missing_grant:
            issues.append(
                f"Execution authorization grant {index} missing fields: "
                + ", ".join(missing_grant)
            )
        if grant.get("iteration") != index + 1:
            issues.append(f"Execution authorization grant {index} has invalid iteration")
        if grant.get("envelope_digest") != envelope.get("approval_digest"):
            issues.append(f"Execution authorization grant {index} has invalid Envelope digest")
        if not _is_iso_timestamp(grant.get("authorized_at")):
            issues.append(f"Execution authorization grant {index} has invalid timestamp")
    return issues


def _normalize_authorization_target(
    unit_dir: Path,
    target: str,
) -> tuple[str | None, str | None]:
    if not isinstance(target, str) or not target.strip():
        return None, "Authorization requires a non-empty target"
    try:
        receipt = _unit_json(unit_dir, "context-receipt.json")
    except ValueError as exc:
        return None, str(exc)
    source_manifest = receipt.get("source_manifest")
    if not isinstance(source_manifest, str) or not source_manifest.strip():
        return None, "Context Receipt has no source_manifest for target authorization"
    manifest_path = Path(source_manifest).expanduser().resolve()
    if manifest_path.name != "project.json":
        return None, "Context Receipt source_manifest is not project.json"
    project_root = manifest_path.parent
    requested = Path(target).expanduser()
    candidate = requested.resolve() if requested.is_absolute() else (project_root / requested).resolve()
    try:
        relative = candidate.relative_to(project_root)
    except ValueError:
        return None, f"Target escapes the selected Project: {target}"
    if relative == Path("."):
        return None, "Authorization target must identify a path inside the Project"
    return relative.as_posix(), None


def _authorization_target_protection_issue(
    unit_dir: Path,
    action: str,
    normalized_target: str,
) -> str | None:
    if action != "edit":
        return None
    target_parts = Path(normalized_target).parts
    if not target_parts:
        return "Authorization target must identify a path inside the Project"
    if normalized_target in {"project.json", "isekai.lock.json"}:
        return f"Core control artifact cannot be edited through authorize: {normalized_target}"
    if target_parts and target_parts[0] in {".git", ".isekai"}:
        return f"Managed control path cannot be edited through authorize: {normalized_target}"
    receipt = _unit_json(unit_dir, "context-receipt.json")
    project_root = Path(str(receipt["source_manifest"])).expanduser().resolve().parent
    candidate = (project_root / normalized_target).resolve()
    # Identify a Unit control artifact by what it is, not by where the active
    # Unit happens to sit. This protects sibling Units in the same Project and
    # stays correct when a Unit is stored outside the Project it governs.
    if (
        candidate.name in PROTECTED_UNIT_ARTIFACTS
        and (candidate.parent / "unit.json").is_file()
    ):
        return f"Unit control artifact cannot be edited through authorize: {normalized_target}"
    if candidate.name == UNIT_LOCK_NAME:
        return f"Unit lock cannot be edited through authorize: {normalized_target}"
    return None


def authorize_action(
    path: str | Path,
    *,
    action: str,
    target: str | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        return {"allowed": False, "reason": f"Unit directory does not exist: {unit_dir}"}
    if action in AGENT_PROHIBITED_ACTIONS:
        return {
            "allowed": False,
            "reason": f"Action is forbidden by the local Agent contract: {action}",
        }
    if action not in AGENT_ALLOWED_ACTIONS:
        return {
            "allowed": False,
            "reason": f"Action is not supported by the local Agent contract: {action}",
        }
    try:
        unit = _unit_json(unit_dir, "unit.json")
        envelope = _unit_json(unit_dir, "execution-envelope.json")
    except ValueError as exc:
        return {"allowed": False, "reason": str(exc)}
    preflight = _unit_preflight_issues(unit_dir)
    if preflight:
        return {"allowed": False, "reason": "Action preflight blocked: " + "; ".join(preflight)}
    envelope_issues = _execution_envelope_issues(
        envelope,
        str(unit.get("id")),
        require_approved=True,
    )
    if envelope_issues:
        return {"allowed": False, "reason": "Action blocked: " + "; ".join(envelope_issues)}
    decision_issues = _approved_envelope_decision_issues(unit_dir, envelope, unit)
    if decision_issues:
        return {
            "allowed": False,
            "reason": "Action blocked: " + "; ".join(decision_issues),
        }
    if action in envelope["forbidden_actions"]:
        return {"allowed": False, "reason": f"Action is forbidden by the Execution Envelope: {action}"}
    if action not in envelope["allowed_actions"]:
        return {"allowed": False, "reason": f"Action is not allowed by the Execution Envelope: {action}"}
    current_stage = unit.get("phase")
    if stage is not None and stage != current_stage:
        return {
            "allowed": False,
            "reason": (
                f"Requested stage {stage} does not match the Unit phase: {current_stage}"
            ),
        }
    stage_matches = [item for item in envelope["stages"] if item.get("name") == current_stage]
    if not stage_matches:
        return {"allowed": False, "reason": f"No approved Envelope stage for: {current_stage}"}
    if action not in stage_matches[0].get("allowed_actions", []):
        return {"allowed": False, "reason": f"Action is not allowed in stage {current_stage}: {action}"}
    normalized_target, target_issue = _normalize_authorization_target(
        unit_dir, str(target) if target is not None else ""
    )
    if target_issue is not None:
        return {"allowed": False, "reason": target_issue}
    assert normalized_target is not None  # narrowed by the fail-closed result above
    protection_issue = _authorization_target_protection_issue(
        unit_dir, action, normalized_target
    )
    if protection_issue is not None:
        return {"allowed": False, "reason": protection_issue}
    if not any(
        fnmatch.fnmatchcase(normalized_target, pattern.replace("\\", "/"))
        for pattern in envelope["scope"]
    ):
        return {
            "allowed": False,
            "reason": f"Target is outside the approved Envelope scope: {normalized_target}",
        }

    ledger_path = unit_dir / "execution-authorizations.json"
    try:
        with unit_lock(unit_dir):
            try:
                ledger = _unit_json(unit_dir, "execution-authorizations.json")
            except ValueError as exc:
                return {"allowed": False, "reason": str(exc)}
            ledger_issues = _authorization_ledger_issues(ledger, unit, envelope)
            if ledger_issues:
                return {
                    "allowed": False,
                    "reason": "Action blocked: " + "; ".join(ledger_issues),
                }
            grants = ledger["grants"]
            if len(grants) >= envelope["max_iterations"]:
                return {
                    "allowed": False,
                    "reason": "Execution Envelope max_iterations budget is exhausted",
                }
            now = datetime.now(timezone.utc)
            iteration = len(grants) + 1
            grant = {
                "id": "AUTH-" + now.strftime("%Y%m%d%H%M%S%f"),
                "action": action,
                "target": normalized_target,
                "stage": current_stage,
                "iteration": iteration,
                "decision_id": envelope.get("approval_decision_id"),
                "envelope_digest": envelope.get("approval_digest"),
                "authorized_at": now.isoformat(),
            }
            grants.append(grant)
            _write_json(ledger_path, ledger)
            persisted = _unit_json(unit_dir, "execution-authorizations.json")
            persisted_issues = _authorization_ledger_issues(persisted, unit, envelope)
            if persisted_issues or persisted.get("grants", [])[-1].get("id") != grant["id"]:
                return {
                    "allowed": False,
                    "reason": "Authorization receipt postflight failed",
                }
            return {
                "allowed": True,
                "reason": "Action is within the approved Execution Envelope",
                "unit_id": unit.get("id"),
                "stage": current_stage,
                "action": action,
                "target": normalized_target,
                "iteration": iteration,
                "remaining_iterations": envelope["max_iterations"] - iteration,
                "authorization_id": grant["id"],
            }
    except LockUnavailable as exc:
        return {"allowed": False, "reason": str(exc)}


LIFECYCLE_STATUSES = (
    "proposed",
    "inception",
    "awaiting-inception-decision",
    "construction",
    "awaiting-release-decision",
    "releasing",
    "operating",
    "learned",
)

ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "proposed": ("inception",),
    "inception": ("awaiting-inception-decision",),
    "awaiting-inception-decision": ("construction",),
    "construction": ("awaiting-release-decision",),
    "awaiting-release-decision": ("releasing",),
    "releasing": ("operating",),
    "operating": ("learned",),
    "learned": (),
}

DECISION_GATES = ("inception", "architecture", "release", "operation", "knowledge")
DECISION_OUTCOMES = ("approved", "rejected")
REQUIRED_DECISIONS_FOR_TRANSITIONS = {
    "construction": "inception",
    "awaiting-release-decision": "architecture",
    "releasing": "release",
    "learned": "operation",
}
STATUS_PHASE = {
    "proposed": "inception",
    "inception": "inception",
    "awaiting-inception-decision": "inception",
    "construction": "construction",
    "awaiting-release-decision": "construction",
    "releasing": "release",
    "operating": "operations",
    "learned": "operations",
}
DECISION_PACKET_VERSION = "1.0.0"
DECISION_REQUIRED_FIELDS = {
    "id",
    "type",
    "schema_version",
    "unit_id",
    "gate",
    "outcome",
    "summary",
    "scope",
    "decision_packet_version",
    "rationale",
    "alternatives",
    "tradeoffs",
    "risks",
    "references",
    "decided_by",
    "decided_at",
}

DECISION_PACKET_FIELDS = {
    "decision_packet_version",
    "rationale",
    "alternatives",
    "tradeoffs",
    "risks",
    "references",
}


def _decision_packet_issues(decision: Any) -> list[str]:
    if not isinstance(decision, dict):
        return ["Decision Packet must be an object"]
    issues: list[str] = []
    missing = sorted(DECISION_PACKET_FIELDS - decision.keys())
    if missing:
        issues.append(f"Decision Packet missing fields: {', '.join(missing)}")
    if decision.get("decision_packet_version") != DECISION_PACKET_VERSION:
        issues.append("Decision Packet has an unsupported version")
    rationale = decision.get("rationale")
    if not isinstance(rationale, list) or not rationale or any(
        not isinstance(item, str) or not item.strip() for item in rationale
    ):
        issues.append("Decision Packet rationale must be a non-empty list of strings")
    for field in ("tradeoffs", "risks", "references"):
        value = decision.get(field)
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            issues.append(f"Decision Packet {field} must be a list of strings")
    alternatives = decision.get("alternatives")
    if not isinstance(alternatives, list):
        issues.append("Decision Packet alternatives must be a list")
    else:
        for index, alternative in enumerate(alternatives):
            if not isinstance(alternative, dict):
                issues.append(f"Decision Packet alternative {index} must be an object")
                continue
            if not isinstance(alternative.get("option"), str) or not alternative["option"].strip():
                issues.append(f"Decision Packet alternative {index} needs option")
            if not isinstance(alternative.get("reason"), str) or not alternative["reason"].strip():
                issues.append(f"Decision Packet alternative {index} needs reason")
    return issues


def _is_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _decision_record_issues(
    decision: Any,
    *,
    unit_id: str | None = None,
    scope: str | None = None,
) -> list[str]:
    if not isinstance(decision, dict):
        return ["Decision must be an object"]
    issues: list[str] = []
    missing = sorted(DECISION_REQUIRED_FIELDS - decision.keys())
    if missing:
        issues.append(f"Decision missing fields: {', '.join(missing)}")
    if decision.get("type") != "human-decision":
        issues.append("Decision has an invalid type")
    if decision.get("schema_version") != "1.0.0":
        issues.append("Decision has an unsupported schema_version")
    if unit_id is not None and decision.get("unit_id") != unit_id:
        issues.append("Decision unit_id does not match Unit")
    if decision.get("gate") not in DECISION_GATES:
        issues.append("Decision has an invalid gate")
    if decision.get("outcome") not in DECISION_OUTCOMES:
        issues.append("Decision has an invalid outcome")
    for field in ("id", "summary", "scope", "decided_by"):
        if not isinstance(decision.get(field), str) or not decision.get(field, "").strip():
            issues.append(f"Decision requires a non-empty {field}")
    if scope is not None and decision.get("scope") != scope:
        issues.append("Decision scope does not match Unit")
    if not _is_iso_timestamp(decision.get("decided_at")):
        issues.append("Decision decided_at must be an ISO-8601 timestamp")
    if decision.get("gate") == "inception" and decision.get("outcome") == "approved":
        approval_subject = decision.get("approval_subject")
        if not isinstance(approval_subject, dict):
            issues.append("approved Inception Decision requires approval_subject")
        else:
            if approval_subject.get("type") != "execution-envelope":
                issues.append("Inception Decision approval_subject type is invalid")
            if not isinstance(approval_subject.get("id"), str) or not approval_subject.get(
                "id", ""
            ).strip():
                issues.append("Inception Decision approval_subject requires id")
            if not isinstance(approval_subject.get("digest"), str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", approval_subject.get("digest", "")
            ):
                issues.append("Inception Decision approval_subject requires SHA-256 digest")
    issues.extend(_decision_packet_issues(decision))
    return issues

def _latest_decision(decisions: dict[str, Any], gate: str) -> dict[str, Any] | None:
    entries = decisions.get("decisions", [])
    if not isinstance(entries, list):
        return None
    for entry in reversed(entries):
        if isinstance(entry, dict) and entry.get("gate") == gate:
            return entry
    return None


def _has_approved_decision(
    decisions: dict[str, Any],
    gate: str,
    *,
    unit_id: str | None = None,
    scope: str | None = None,
) -> bool:
    latest = _latest_decision(decisions, gate)
    return (
        latest is not None
        and latest.get("outcome") == "approved"
        and not _decision_record_issues(latest, unit_id=unit_id, scope=scope)
    )


def _approved_envelope_decision_issues(
    unit_dir: Path,
    envelope: dict[str, Any],
    unit: dict[str, Any],
) -> list[str]:
    if envelope.get("status") != "approved":
        return []
    try:
        decisions = _unit_json(unit_dir, "decisions.json")
    except ValueError as exc:
        return [str(exc)]
    if decisions.get("unit_id") != unit.get("id"):
        return ["decisions.json unit_id does not match Unit"]
    latest = _latest_decision(decisions, "inception")
    if latest is None:
        return ["approved Execution Envelope has no Inception Decision"]
    issues = _decision_record_issues(
        latest,
        unit_id=str(unit.get("id")),
        scope=str(unit.get("scope")),
    )
    if latest.get("outcome") != "approved":
        issues.append("approved Execution Envelope was revoked by the latest Inception Decision")
    else:
        approval_subject = latest.get("approval_subject")
        if not isinstance(approval_subject, dict):
            issues.append("latest Inception Decision has no bound Execution Envelope subject")
        else:
            if approval_subject.get("id") != envelope.get("id"):
                issues.append("Execution Envelope id does not match its Inception Decision")
            if approval_subject.get("digest") != envelope.get("approval_digest"):
                issues.append("Execution Envelope digest does not match its Inception Decision")
        if latest.get("id") != envelope.get("approval_decision_id"):
            issues.append("Execution Envelope approval does not match the latest Inception Decision")
    return issues


EVIDENCE_REQUIRED_FIELDS = {
    "id",
    "type",
    "schema_version",
    "unit_id",
    "passed",
    "scope",
    "recorded_by",
    "recorded_at",
    "commands",
}
EVIDENCE_COMMAND_REQUIRED_FIELDS = {
    "command",
    "exit_code",
    "output_digest",
    "observed_at",
}


def _evidence_issues(
    evidence: Any,
    unit_id: str | None = None,
    *,
    require_passing: bool = True,
) -> list[str]:
    if not isinstance(evidence, dict):
        return ["verification evidence must be an object"]
    issues: list[str] = []
    missing_fields = sorted(EVIDENCE_REQUIRED_FIELDS - evidence.keys())
    if missing_fields:
        issues.append(
            f"verification evidence missing fields: {', '.join(missing_fields)}"
        )
    if unit_id is not None and evidence.get("unit_id") != unit_id:
        issues.append("verification evidence unit_id does not match Unit")
    if evidence.get("type") != "verification-evidence":
        issues.append("verification evidence has an invalid type")
    if evidence.get("schema_version") != "1.0.0":
        issues.append("verification evidence has an unsupported schema_version")
    if not isinstance(evidence.get("passed"), bool):
        issues.append("verification evidence passed must be boolean")
    if not isinstance(evidence.get("scope"), str) or not evidence.get("scope", "").strip():
        issues.append("verification evidence requires a scope")
    if not isinstance(evidence.get("recorded_by"), str) or not evidence.get("recorded_by", "").strip():
        issues.append("verification evidence requires recorded_by provenance")
    if not _is_iso_timestamp(evidence.get("recorded_at")):
        issues.append("verification evidence recorded_at must be an ISO-8601 timestamp")

    commands = evidence.get("commands")
    if not isinstance(commands, list) or not commands:
        issues.append("verification evidence has no commands")
    else:
        for index, command in enumerate(commands):
            if not isinstance(command, dict):
                issues.append(f"evidence command {index} must be an object")
                continue
            missing_command_fields = sorted(
                EVIDENCE_COMMAND_REQUIRED_FIELDS - command.keys()
            )
            if missing_command_fields:
                issues.append(
                    f"evidence command {index} missing fields: "
                    f"{', '.join(missing_command_fields)}"
                )
                continue
            if not isinstance(command.get("command"), str) or not command["command"].strip():
                issues.append(f"evidence command {index} requires command text")
            exit_code = command.get("exit_code")
            if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                issues.append(f"evidence command {index} exit_code must be an integer")
            digest = command.get("output_digest")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                issues.append(
                    f"evidence command {index} output_digest must be a SHA-256 hex digest"
                )
            if not _is_iso_timestamp(command.get("observed_at")):
                issues.append(
                    f"evidence command {index} observed_at must be an ISO-8601 timestamp"
                )
            if evidence.get("passed") is True and exit_code != 0:
                issues.append(
                    f"passing verification evidence has non-zero command {index} exit_code"
                )
    if require_passing and evidence.get("passed") is not True:
        issues.append("verification evidence is not passing")
    return issues


def _passing_evidence(unit_dir: Path) -> bool:
    evidence_path = unit_dir / "evidence/verification.json"
    if not evidence_path.is_file():
        return False
    try:
        evidence = _unit_json(unit_dir, "evidence/verification.json")
        unit = _unit_json(unit_dir, "unit.json")
    except ValueError:
        return False
    return not _evidence_issues(evidence, str(unit.get("id")))


def build_command_evidence(
    command: str,
    exit_code: int,
    output: str,
    observed_at: str,
) -> dict[str, Any]:
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise ValueError("exit_code must be an integer")
    if not isinstance(output, str):
        raise ValueError("output must be a string")
    if not isinstance(observed_at, str) or not observed_at.strip():
        raise ValueError("observed_at must be a non-empty string")
    return {
        "command": command.strip(),
        "exit_code": exit_code,
        "output_digest": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "observed_at": observed_at.strip(),
    }


def record_evidence(
    path: str | Path,
    *,
    passed: bool,
    commands: list[dict[str, Any]],
    scope: str,
    recorded_by: str,
    notes: str = "",
) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise ValueError(f"Unit directory does not exist: {unit_dir}")
    if not isinstance(passed, bool):
        raise ValueError("passed must be boolean")
    if not isinstance(commands, list) or not commands:
        raise ValueError("commands must be a non-empty list")
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("scope must be a non-empty string")
    if not isinstance(recorded_by, str) or not recorded_by.strip():
        raise ValueError("recorded_by must be a non-empty string")

    with unit_lock(unit_dir):
        return _record_evidence_locked(
            unit_dir,
            passed=passed,
            commands=commands,
            scope=scope,
            recorded_by=recorded_by,
            notes=notes,
        )


def _record_evidence_locked(
    unit_dir: Path,
    *,
    passed: bool,
    commands: list[dict[str, Any]],
    scope: str,
    recorded_by: str,
    notes: str,
) -> dict[str, Any]:
    unit = _unit_json(unit_dir, "unit.json")
    preflight_issues = _unit_preflight_issues(unit_dir)
    if preflight_issues:
        raise ValueError("Evidence preflight blocked: " + "; ".join(preflight_issues))
    normalized_commands: list[dict[str, Any]] = []
    for index, command in enumerate(commands):
        if not isinstance(command, dict):
            raise ValueError(f"command {index} must be an object")
        item = dict(command)
        if "output" in item:
            output = item.pop("output")
            computed = build_command_evidence(
                str(item.get("command", "")),
                item.get("exit_code"),
                output,
                str(item.get("observed_at", "")),
            )
            supplied_digest = item.get("output_digest")
            if supplied_digest is not None and supplied_digest != computed["output_digest"]:
                raise ValueError(f"command {index} output_digest does not match output")
            item.update(computed)
        normalized_commands.append(item)

    now = datetime.now(timezone.utc)
    evidence = {
        "id": "EVD-" + now.strftime("%Y%m%d%H%M%S%f"),
        "type": "verification-evidence",
        "schema_version": "1.0.0",
        "unit_id": unit.get("id"),
        "passed": passed,
        "scope": scope.strip(),
        "recorded_by": recorded_by.strip(),
        "recorded_at": now.isoformat(),
        "commands": normalized_commands,
    }
    if notes.strip():
        evidence["notes"] = notes.strip()
    issues = _evidence_issues(
        evidence,
        str(unit.get("id")),
        require_passing=False,
    )
    if issues:
        raise ValueError("; ".join(issues))
    _write_json(unit_dir / "evidence/verification.json", evidence)
    persisted_evidence = _unit_json(unit_dir, "evidence/verification.json")
    if persisted_evidence.get("id") != evidence["id"]:
        raise ValueError("Evidence postflight blocked: record was not persisted")
    return {"path": str(unit_dir / "evidence/verification.json"), "evidence": evidence}


def record_decision(
    path: str | Path,
    *,
    gate: str,
    outcome: str,
    summary: str,
    rationale: list[str],
    alternatives: list[dict[str, Any]],
    tradeoffs: list[str],
    risks: list[str],
    references: list[str],
    decided_by: str,
) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise ValueError(f"Unit directory does not exist: {unit_dir}")
    if gate not in DECISION_GATES:
        raise ValueError(f"gate must be one of: {', '.join(DECISION_GATES)}")
    if outcome not in DECISION_OUTCOMES:
        raise ValueError(f"outcome must be one of: {', '.join(DECISION_OUTCOMES)}")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary must be a non-empty string")
    if not isinstance(decided_by, str) or not decided_by.strip():
        raise ValueError("decided_by must be a non-empty string")
    packet_issues = _decision_packet_issues(
        {
            "decision_packet_version": DECISION_PACKET_VERSION,
            "rationale": rationale,
            "alternatives": alternatives,
            "tradeoffs": tradeoffs,
            "risks": risks,
            "references": references,
        }
    )
    if packet_issues:
        raise ValueError("Decision Packet rejected: " + "; ".join(packet_issues))

    with unit_lock(unit_dir):
        unit = _unit_json(unit_dir, "unit.json")
        preflight_issues = _unit_preflight_issues(unit_dir)
        if preflight_issues:
            raise ValueError("Decision preflight blocked: " + "; ".join(preflight_issues))
        decisions = _unit_json(unit_dir, "decisions.json")
        entries = decisions.get("decisions")
        if not isinstance(entries, list):
            raise ValueError("decisions.json decisions must be a list")
        if decisions.get("unit_id") != unit.get("id"):
            raise ValueError("decisions.json unit_id does not match Unit")
        for index, existing in enumerate(entries):
            existing_issues = _decision_record_issues(
                existing,
                unit_id=str(unit.get("id")),
                scope=str(unit.get("scope")),
            )
            if existing_issues:
                raise ValueError(
                    f"existing Decision {index} is invalid: " + "; ".join(existing_issues)
                )
        preceding_ids = [entry.get("id") for entry in entries]

        approval_subject: dict[str, str] | None = None
        if gate == "inception" and outcome == "approved":
            if "execution-envelope.json" not in references:
                raise ValueError(
                    "approved Inception Decision must reference execution-envelope.json"
                )
            envelope = _unit_json(unit_dir, "execution-envelope.json")
            envelope_issues = _execution_envelope_issues(
                envelope, str(unit.get("id"))
            )
            if envelope_issues:
                raise ValueError(
                    "Inception Decision cannot bind an invalid Execution Envelope: "
                    + "; ".join(envelope_issues)
                )
            approval_subject = {
                "type": "execution-envelope",
                "id": str(envelope["id"]),
                "digest": str(envelope["approval_digest"]),
            }

        now = datetime.now(timezone.utc)
        decision = {
            "id": "DEC-" + now.strftime("%Y%m%d%H%M%S%f"),
            "type": "human-decision",
            "schema_version": "1.0.0",
            "unit_id": unit.get("id"),
            "gate": gate,
            "outcome": outcome,
            "summary": summary.strip(),
            "scope": unit["scope"],
            "decision_packet_version": DECISION_PACKET_VERSION,
            "rationale": rationale,
            "alternatives": alternatives,
            "tradeoffs": tradeoffs,
            "risks": risks,
            "references": references,
            "decided_by": decided_by.strip(),
            "decided_at": now.isoformat(),
        }
        if approval_subject is not None:
            decision["approval_subject"] = approval_subject
        entries.append(decision)
        decisions["unit_id"] = unit.get("id")
        _write_json(unit_dir / "decisions.json", decisions)
        persisted_entries = _unit_json(unit_dir, "decisions.json").get("decisions", [])
        persisted_ids = [entry.get("id") for entry in persisted_entries]
        # Check that no earlier record was dropped, not merely that this one
        # landed. A lost update leaves the winner's record last and looks fine.
        if persisted_ids != [*preceding_ids, decision["id"]]:
            raise ValueError(
                "Decision postflight blocked: the Decision ledger changed during the write"
            )
    return {"path": str(unit_dir / "decisions.json"), "decision": decision}


def transition_unit(path: str | Path, target_status: str) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise ValueError(f"Unit directory does not exist: {unit_dir}")
    with unit_lock(unit_dir):
        return _transition_unit_locked(unit_dir, target_status)


def _transition_unit_locked(unit_dir: Path, target_status: str) -> dict[str, Any]:
    preflight_issues = _unit_preflight_issues(unit_dir)
    if preflight_issues:
        raise ValueError("Unit preflight blocked: " + "; ".join(preflight_issues))
    if target_status not in LIFECYCLE_STATUSES:
        raise ValueError(
            f"target_status must be one of: {', '.join(LIFECYCLE_STATUSES)}"
        )

    unit = _unit_json(unit_dir, "unit.json")
    current_status = unit.get("status")
    if current_status not in LIFECYCLE_STATUSES:
        raise ValueError(f"Unit has an invalid lifecycle status: {current_status}")
    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        raise ValueError(
            f"invalid lifecycle transition: {current_status} -> {target_status}"
        )

    required_gate = REQUIRED_DECISIONS_FOR_TRANSITIONS.get(target_status)
    if required_gate:
        decisions = _unit_json(unit_dir, "decisions.json")
        if not _has_approved_decision(
            decisions,
            required_gate,
            unit_id=str(unit.get("id")),
            scope=str(unit.get("scope")),
        ):
            raise ValueError(
                f"transition to {target_status} requires an approved "
                f"{required_gate} Decision"
            )

    if target_status == "construction":
        decisions = _unit_json(unit_dir, "decisions.json")
        inception_decision = _latest_decision(decisions, "inception")
        if inception_decision is None or inception_decision.get("outcome") != "approved":
            raise ValueError("Execution Envelope needs an approved inception Decision")
        _approve_execution_envelope(unit_dir, inception_decision)

    if target_status == "releasing" and not _passing_evidence(unit_dir):
        raise ValueError(
            "transition to releasing requires passing verification Evidence"
        )

    unit["status"] = target_status
    unit["phase"] = STATUS_PHASE[target_status]
    unit["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(unit_dir / "unit.json", unit)
    persisted = _unit_json(unit_dir, "unit.json")
    if persisted.get("status") != target_status:
        raise ValueError("Unit postflight blocked: lifecycle status was not persisted")
    return {
        "unit_id": unit.get("id"),
        "from": current_status,
        "to": target_status,
        "phase": unit["phase"],
        "required_gate": required_gate,
    }


def verify_unit(path: str | Path) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise ValueError(f"Unit directory does not exist: {unit_dir}")
    present = {
        str(file.relative_to(unit_dir))
        for file in unit_dir.rglob("*")
        if file.is_file()
        and "__pycache__" not in file.parts
        and not file.name.startswith(UNIT_LOCK_NAME)
    }
    missing = sorted(UNIT_REQUIRED_FILES - present)
    issues: list[str] = []

    def read_artifact(relative: str) -> dict[str, Any] | None:
        try:
            return _unit_json(unit_dir, relative)
        except ValueError as exc:
            issues.append(str(exc))
            return None

    unit = read_artifact("unit.json") or {}
    decisions = read_artifact("decisions.json")
    checkpoint = read_artifact("checkpoint.json")
    issues.extend(_unit_preflight_issues(unit_dir))
    envelope_path = unit_dir / "execution-envelope.json"
    envelope: dict[str, Any] | None = None
    if envelope_path.is_file():
        envelope = read_artifact("execution-envelope.json")
        if envelope is not None:
            # Verification audits structure and binding, not whether the
            # approval window is still open, so a Unit stays verifiable after
            # its Envelope lapses.
            issues.extend(
                _execution_envelope_issues(
                    envelope, str(unit.get("id")), check_expiry=False
                )
            )
            issues.extend(_approved_envelope_decision_issues(unit_dir, envelope, unit))
    ledger_path = unit_dir / "execution-authorizations.json"
    if ledger_path.is_file() and envelope is not None:
        ledger = read_artifact("execution-authorizations.json")
        if ledger is not None:
            issues.extend(_authorization_ledger_issues(ledger, unit, envelope))

    decision_entries = decisions.get("decisions") if decisions is not None else None
    if decisions is not None:
        if decisions.get("unit_id") != unit.get("id"):
            issues.append("decisions.json unit_id does not match Unit")
    if decisions is not None and not isinstance(decision_entries, list):
        issues.append("decisions.json decisions must be a list")
    elif isinstance(decision_entries, list) and not decision_entries:
        issues.append("at least one recorded decision is required")
    elif isinstance(decision_entries, list):
        for index, decision in enumerate(decision_entries):
            issues.extend(
                f"decision {index}: {issue}"
                for issue in _decision_record_issues(
                    decision,
                    unit_id=str(unit.get("id")),
                    scope=str(unit.get("scope")),
                )
            )

    status = unit.get("status")
    if status not in LIFECYCLE_STATUSES:
        issues.append(f"invalid lifecycle status: {status}")
    required_gate = REQUIRED_DECISIONS_FOR_TRANSITIONS.get(status)
    if required_gate and isinstance(decision_entries, list):
        if not _has_approved_decision(decisions, required_gate):
            issues.append(
                f"status {status} requires an approved {required_gate} Decision"
            )
    if status in STATUS_PHASE and unit.get("phase") != STATUS_PHASE[status]:
        issues.append("Unit phase does not match lifecycle status")
    if checkpoint is not None:
        if checkpoint.get("unit_id") != unit.get("id"):
            issues.append("checkpoint unit_id does not match Unit")
        if checkpoint.get("blocked_by"):
            issues.append("checkpoint has blockers")
        if unit.get("status") == "learned" and checkpoint.get("pending"):
            issues.append("learned Unit cannot have pending work")

    acceptance_path = unit_dir / "acceptance.md"
    if acceptance_path.is_file() and "- [ ]" in acceptance_path.read_text(encoding="utf-8"):
        issues.append("acceptance criteria remain unchecked")

    criteria_path = unit_dir / "evaluations/criteria.json"
    if criteria_path.is_file():
        criteria = read_artifact("evaluations/criteria.json")
        if criteria is not None and criteria.get("visibility") != "evaluation-only":
            issues.append("evaluation criteria must be evaluation-only")

    evidence_path = unit_dir / "evidence/verification.json"
    evidence: dict[str, Any] | None = None
    if evidence_path.is_file():
        evidence = read_artifact("evidence/verification.json")
        if evidence is not None:
            issues.extend(_evidence_issues(evidence, str(unit.get("id"))))

    issues = list(dict.fromkeys(issues))
    valid = not missing and not issues
    return {
        "valid": valid,
        "unit_id": unit.get("id"),
        "phase": unit.get("phase"),
        "status": unit.get("status"),
        "artifact_count": len(present),
        "missing": missing,
        "issues": issues,
        "decision_count": len(decision_entries) if isinstance(decision_entries, list) else 0,
        "project_id": unit.get("project_id"),
        "foundation_version": unit.get("foundation_version"),
        "foundation_digest": unit.get("foundation_digest"),
        "pending": checkpoint.get("pending", []) if checkpoint is not None else [],
        "blocked_by": checkpoint.get("blocked_by", []) if checkpoint is not None else [],
        "evidence": evidence,
    }


def unit_status(path: str | Path) -> dict[str, Any]:
    result = verify_unit(path)
    result["unit_dir"] = str(Path(path).resolve())
    return result
