from __future__ import annotations

import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from isekai.support.errors import WorkflowError
from isekai.catalog.ai_dlc.intake import normalize_intent
from isekai.workflow.project import portable_context_receipt as _portable_context_receipt, resolve_context
from isekai.workflow.project_knowledge import (
    current_project_knowledge,
    select_project_knowledge_context,
)
from isekai.catalog.ai_dlc.routing import AGENT_PROHIBITED_ACTIONS, WorkRoute
from .authorization import authorization_ledger_digest as _authorization_ledger_digest
from .artifacts import unit_document_templates
from .checkpointing import authorization_progress_cursor
from .common import write_json as _write_json
from .execution import EXECUTION_ENVELOPE_DEFAULT_HOURS
from .execution_schema import execution_envelope_approval_digest


def _validated_title(value: str) -> str:
    if not isinstance(value, str):
        raise WorkflowError("title must contain at least one letter or number")
    title = value.strip()
    if not title or not re.search(r"\w", title, flags=re.UNICODE):
        raise WorkflowError("title must contain at least one letter or number")
    return title


def _validated_owner(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError("owner must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class _UnitInitializationPlan:
    title: str
    owner: str
    catalog_entry: str
    receipt: dict[str, Any]
    project_root: Path
    output_root: Path
    final_unit_dir: Path
    unit_id: str
    normalized_intent: dict[str, Any]
    document_language: str


@dataclass(frozen=True)
class _UnitDocumentPlan:
    intent: str
    templates: dict[str, str]
    pending: list[str]
    next_action: str


def _validated_catalog_entry(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError("catalog_entry must be a non-empty string")
    if value != "ai-dlc":
        raise WorkflowError(
            "AI-DLC Unit initialization requires catalog_entry ai-dlc; "
            "other Catalog entries must use their own resource model"
        )
    return value


def _resolved_output_root(
    project_root: Path,
    output_root: str | Path | None,
) -> Path:
    requested = Path("units") if output_root is None else Path(output_root).expanduser()
    resolved = (
        requested.resolve()
        if requested.is_absolute()
        else (project_root / requested).resolve()
    )
    if not requested.is_absolute():
        try:
            resolved.relative_to(project_root)
        except ValueError as exc:
            raise WorkflowError(
                f"relative Unit output escapes project root: {requested}"
            ) from exc
    return resolved


def _initialization_plan(
    project_path: str | Path,
    *,
    title: str,
    owner: str,
    output_root: str | Path | None,
    intent: dict[str, Any] | None,
    catalog_entry: str,
) -> _UnitInitializationPlan:
    if intent is not None and not isinstance(intent, dict):
        raise WorkflowError("intent must be an object")
    receipt = resolve_context(project_path, WorkRoute.UNIT)
    catalog = receipt.get("catalog", {})
    catalog_entries = catalog.get("entries", [])
    active_ids = [
        entry["id"]
        for entry in catalog_entries
        if isinstance(entry, dict) and entry.get("active")
    ]
    if "ai-dlc" not in active_ids:
        raise WorkflowError(
            "AI-DLC Unit initialization requires the active ai-dlc Catalog entry"
        )
    project_root = Path(str(receipt["source_manifest"])).resolve().parent
    resolved_output_root = _resolved_output_root(project_root, output_root)
    normalized_intent = normalize_intent({"goal": title, **(intent or {})})
    work_scope = normalized_intent["scope"]
    receipt["project_knowledge"] = select_project_knowledge_context(
        current_project_knowledge(project_root, str(receipt["project_id"])),
        work_scope,
    )
    unit_id = (
        f"UNIT-{datetime.now(timezone.utc).strftime('%Y%m%d')}-"
        f"{uuid.uuid4().hex.upper()}"
    )
    resolved_output_root.mkdir(parents=True, exist_ok=True)
    final_unit_dir = resolved_output_root / unit_id.lower()
    if final_unit_dir.exists():
        raise FileExistsError(f"unit already exists: {final_unit_dir}")
    portable_receipt = _portable_context_receipt(
        receipt,
        project_root=project_root,
        unit_dir=final_unit_dir,
    )
    return _UnitInitializationPlan(
        title=title,
        owner=owner,
        catalog_entry=catalog_entry,
        receipt=portable_receipt,
        project_root=project_root,
        output_root=resolved_output_root,
        final_unit_dir=final_unit_dir,
        unit_id=unit_id,
        normalized_intent=normalized_intent,
        document_language=str(portable_receipt["document_language"]),
    )


def _document_plan(plan: _UnitInitializationPlan) -> _UnitDocumentPlan:
    intent = plan.normalized_intent
    if plan.document_language == "ko":
        headings = {
            "goal": "## 목표",
            "outcome": "## 기대 결과",
            "scope": "## 범위",
            "constraints": "## 제약사항",
            "acceptance": "## 인수 조건",
        }
        placeholders = (
            "기대 결과를 정의합니다.",
            "- 작업 범위를 정의합니다.",
            "- 제약사항을 정의합니다.",
            "- [ ] 검증 가능한 인수 조건을 정의합니다.",
        )
        pending = ["Inception 내용 구체화"]
        next_action = "의도와 인수 조건을 구체화합니다."
        templates = unit_document_templates("ko")
    else:
        headings = {
            "goal": "## Goal",
            "outcome": "## Expected outcome",
            "scope": "## Scope",
            "constraints": "## Constraints",
            "acceptance": "## Acceptance criteria",
        }
        placeholders = (
            "Define the expected outcome.",
            "- Define the work scope.",
            "- Define constraints.",
            "- [ ] Define verifiable acceptance criteria.",
        )
        pending = ["inception elaboration"]
        next_action = "clarify intent and acceptance criteria"
        templates = unit_document_templates("en")
    expected_placeholder, scope_placeholder, constraint_placeholder, acceptance_placeholder = placeholders
    scope_lines = [f"- {item}" for item in intent["scope"]] or [scope_placeholder]
    constraint_lines = [f"- {item}" for item in intent["constraints"]] or [
        constraint_placeholder
    ]
    acceptance_lines = [
        f"- [ ] {item}" for item in intent["acceptance_criteria"]
    ] or [acceptance_placeholder]
    lines = [
        f"# {plan.title}", "", headings["goal"], "", intent["goal"], "",
        headings["outcome"], "", intent["expected_outcome"] or expected_placeholder,
        "", headings["scope"], "", *scope_lines, "", headings["constraints"],
        "", *constraint_lines, "", headings["acceptance"], "",
        *acceptance_lines, "",
    ]
    return _UnitDocumentPlan(
        intent="\n".join(lines),
        templates=templates,
        pending=pending,
        next_action=next_action,
    )


def _write_unit_documents(
    unit_dir: Path,
    plan: _UnitInitializationPlan,
    documents: _UnitDocumentPlan,
) -> None:
    intent = plan.normalized_intent
    _write_json(
        unit_dir / "unit.json",
        {
            "id": plan.unit_id,
            "catalog_entry": plan.catalog_entry,
            "title": plan.title,
            "project_id": plan.receipt["project_id"],
            "phase": "inception",
            "status": "proposed",
            "owner": plan.owner,
            "scope": f"project:{plan.receipt['project_id']}",
            "work_scope": intent["scope"],
            "intent_source": intent["source"],
            "document_language": plan.document_language,
            "goal": intent["goal"],
            "expected_outcome": intent["expected_outcome"],
            "constraints": intent["constraints"],
            "acceptance_criteria": intent["acceptance_criteria"],
            "intake": {
                key: intent[key]
                for key in (
                    "change", "risk", "ambiguous", "multi_party", "remote",
                    "sensitive", "classification",
                )
            },
            "foundation_version": plan.receipt["foundation_version"],
            "foundation_digest": plan.receipt["foundation_digest"],
        },
    )
    (unit_dir / "intent.md").write_text(documents.intent, encoding="utf-8")
    for relative, content in documents.templates.items():
        (unit_dir / relative).write_text(content, encoding="utf-8")
    (unit_dir / "evaluations").mkdir()
    (unit_dir / "evidence").mkdir()
    _write_json(
        unit_dir / "evaluations/criteria.json",
        {"unit_id": plan.unit_id, "visibility": "evaluation-only", "criteria": []},
    )
    _write_json(
        unit_dir / "decisions.json",
        {"unit_id": plan.unit_id, "decisions": []},
    )
    _write_json(
        unit_dir / "amendments.json",
        {
            "type": "unit-amendment-ledger",
            "schema_version": "1.0.0",
            "unit_id": plan.unit_id,
            "amendments": [],
        },
    )


def _initial_execution_contract(
    plan: _UnitInitializationPlan,
) -> tuple[dict[str, Any], dict[str, Any]]:
    now = datetime.now(timezone.utc)
    envelope: dict[str, Any] = {
        "id": f"ENV-{plan.unit_id}-INITIAL",
        "type": "execution-envelope",
        "schema_version": "1.0.0",
        "unit_id": plan.unit_id,
        "status": "proposed",
        "scope": [],
        "stages": [],
        "allowed_actions": [],
        "forbidden_actions": sorted(AGENT_PROHIBITED_ACTIONS),
        "external_access": [],
        "max_iterations": 0,
        "proposed_by": plan.owner,
        "proposed_at": now.isoformat(),
        "expires_at": (
            now + timedelta(hours=EXECUTION_ENVELOPE_DEFAULT_HOURS)
        ).isoformat(),
    }
    envelope["approval_digest"] = execution_envelope_approval_digest(envelope)
    authorizations: dict[str, Any] = {
        "type": "execution-authorization-ledger",
        "schema_version": "1.0.0",
        "unit_id": plan.unit_id,
        "envelope_id": envelope["id"],
        "approval_digest": envelope["approval_digest"],
        "grants": [],
    }
    return envelope, authorizations


def _write_execution_contracts(
    unit_dir: Path,
    plan: _UnitInitializationPlan,
    documents: _UnitDocumentPlan,
) -> None:
    envelope, authorizations = _initial_execution_contract(plan)
    _write_json(unit_dir / "execution-envelope.json", envelope)
    _write_json(unit_dir / "execution-authorizations.json", authorizations)
    _write_json(
        unit_dir / "evidence/verification.json",
        {
            "id": "",
            "type": "verification-evidence",
            "schema_version": "1.0.0",
            "unit_id": plan.unit_id,
            "stage": "inception",
            "passed": False,
            "scope": "",
            "recorded_by": "",
            "recorded_at": "",
            "commands": [],
            "envelope_id": envelope["id"],
            "envelope_digest": envelope["approval_digest"],
            "authorization_ledger_digest": _authorization_ledger_digest(
                authorizations
            ),
            "authorization_count": 0,
        },
    )
    _write_json(
        unit_dir / "checkpoint.json",
        {
            "unit_id": plan.unit_id,
            "completed": [],
            "pending": documents.pending,
            "blocked_by": [],
            "next_action": documents.next_action,
            "authorization_cursor": authorization_progress_cursor(
                unit_dir, authorizations
            ),
        },
    )
    _write_json(unit_dir / "context-receipt.json", plan.receipt)


def _commit_staged_unit(
    staging: tempfile.TemporaryDirectory[str],
    unit_dir: Path,
    final_unit_dir: Path,
    postflight: Callable[[Path], None] | None,
) -> None:
    unit_dir.rename(final_unit_dir)
    try:
        if postflight is not None:
            postflight(final_unit_dir)
    except Exception as exc:
        try:
            final_unit_dir.rename(unit_dir)
            staging.cleanup()
        except Exception as restore_exc:  # pragma: no cover - secondary failure
            raise WorkflowError(
                "Unit initialization postflight failed and the staged Unit "
                f"could not be rolled back: {restore_exc}"
            ) from exc
        raise
    staging.cleanup()


def initialize_unit(
    project_path: str | Path,
    title: str,
    output_root: str | Path | None = None,
    owner: str = "unassigned",
    intent: dict[str, Any] | None = None,
    catalog_entry: str = "ai-dlc",
    _postflight: Callable[[Path], None] | None = None,
) -> Path:
    title = _validated_title(title)
    owner = _validated_owner(owner)
    catalog_entry = _validated_catalog_entry(catalog_entry)
    plan = _initialization_plan(
        project_path,
        title=title,
        owner=owner,
        output_root=output_root,
        intent=intent,
        catalog_entry=catalog_entry,
    )
    documents = _document_plan(plan)
    staging = tempfile.TemporaryDirectory(
        prefix=f".{plan.unit_id.lower()}.stage-",
        dir=plan.output_root,
    )
    unit_dir = Path(staging.name)
    _write_unit_documents(unit_dir, plan, documents)
    _write_execution_contracts(unit_dir, plan, documents)
    _commit_staged_unit(
        staging,
        unit_dir,
        plan.final_unit_dir,
        _postflight,
    )
    return plan.final_unit_dir
