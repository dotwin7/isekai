from __future__ import annotations

import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from isekai.support.errors import WorkflowError
from isekai.catalog.ai_dlc.intake import normalize_intent
from isekai.workflow.project import _portable_context_receipt, resolve_context
from isekai.workflow.project_knowledge import (
    current_project_knowledge,
    select_project_knowledge_context,
)
from isekai.catalog.ai_dlc.routing import AGENT_PROHIBITED_ACTIONS, WorkRoute
from .authorization import _authorization_ledger_digest
from .artifacts import unit_document_templates
from .checkpointing import authorization_progress_cursor
from .common import _write_json
from .execution import (
    EXECUTION_ENVELOPE_DEFAULT_HOURS,
    _execution_envelope_approval_digest,
)


def _validated_title(value: str) -> str:
    title = value.strip()
    if not title or not re.search(r"\w", title, flags=re.UNICODE):
        raise WorkflowError("title must contain at least one letter or number")
    return title

def initialize_unit(
    project_path: str | Path,
    title: str,
    output_root: str | Path | None = None,
    owner: str = "unassigned",
    intent: dict[str, Any] | None = None,
) -> Path:
    title = _validated_title(title)
    receipt = resolve_context(project_path, WorkRoute.UNIT)
    manifest_path = Path(str(receipt["source_manifest"])).resolve()
    project_root = manifest_path.parent.resolve()
    if output_root is None:
        requested_output_root = Path("units")
        resolved_output_root = (project_root / requested_output_root).resolve()
        output_label: str | Path = requested_output_root
    else:
        requested_output_root = Path(output_root).expanduser()
        if requested_output_root.is_absolute():
            resolved_output_root = requested_output_root.resolve()
            output_label = output_root
        else:
            resolved_output_root = (project_root / requested_output_root).resolve()
            output_label = output_root
    if output_root is None or not requested_output_root.is_absolute():
        try:
            resolved_output_root.relative_to(project_root)
        except ValueError as exc:
            raise WorkflowError(
                f"relative Unit output escapes project root: {output_label}"
            ) from exc
    intent_values = dict(intent or {})
    intent_values.setdefault("goal", title)
    normalized_intent = normalize_intent(intent_values)
    goal = normalized_intent["goal"]
    intent_source = normalized_intent["source"]
    expected_outcome = normalized_intent["expected_outcome"]
    work_scope = normalized_intent["scope"]
    constraints = normalized_intent["constraints"]
    acceptance_criteria = normalized_intent["acceptance_criteria"]
    receipt["project_knowledge"] = select_project_knowledge_context(
        current_project_knowledge(project_root, str(receipt["project_id"])),
        work_scope,
    )
    document_language = receipt["document_language"]
    unit_id = (
        f"UNIT-{datetime.now(timezone.utc).strftime('%Y%m%d')}-"
        f"{uuid.uuid4().hex.upper()}"
    )
    output_root = resolved_output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    final_unit_dir = output_root / unit_id.lower()
    if final_unit_dir.exists():
        raise FileExistsError(f"unit already exists: {final_unit_dir}")
    receipt = _portable_context_receipt(
        receipt,
        project_root=project_root,
        unit_dir=final_unit_dir,
    )
    staging = tempfile.TemporaryDirectory(
        prefix=f".{unit_id.lower()}.stage-",
        dir=output_root,
    )
    unit_dir = Path(staging.name)

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
            "intake": {
                "change": normalized_intent["change"],
                "risk": normalized_intent["risk"],
                "ambiguous": normalized_intent["ambiguous"],
                "multi_party": normalized_intent["multi_party"],
                "remote": normalized_intent["remote"],
                "sensitive": normalized_intent["sensitive"],
                "classification": normalized_intent["classification"],
            },
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
        templates = unit_document_templates("ko")
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
        templates = unit_document_templates("en")
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
    _write_json(unit_dir / "decisions.json", {"unit_id": unit_id, "decisions": []})
    _write_json(
        unit_dir / "amendments.json",
        {
            "type": "unit-amendment-ledger",
            "schema_version": "1.0.0",
            "unit_id": unit_id,
            "amendments": [],
        },
    )
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
        "external_access": [],
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
    initial_authorizations = {
        "type": "execution-authorization-ledger",
        "schema_version": "1.0.0",
        "unit_id": unit_id,
        "envelope_id": initial_envelope["id"],
        "approval_digest": initial_envelope["approval_digest"],
        "grants": [],
    }
    _write_json(unit_dir / "execution-authorizations.json", initial_authorizations)
    _write_json(
        unit_dir / "evidence/verification.json",
        {
            "id": "",
            "type": "verification-evidence",
            "schema_version": "1.0.0",
            "unit_id": unit_id,
            "stage": "inception",
            "passed": False,
            "scope": "",
            "recorded_by": "",
            "recorded_at": "",
            "commands": [],
            "envelope_id": initial_envelope["id"],
            "envelope_digest": initial_envelope["approval_digest"],
            "authorization_ledger_digest": _authorization_ledger_digest(
                initial_authorizations
            ),
            "authorization_count": 0,
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
            "authorization_cursor": authorization_progress_cursor(
                unit_dir, initial_authorizations
            ),
        },
    )
    _write_json(unit_dir / "context-receipt.json", receipt)
    unit_dir.rename(final_unit_dir)
    staging.cleanup()
    return final_unit_dir
