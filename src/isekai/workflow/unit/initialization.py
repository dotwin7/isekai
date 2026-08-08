from __future__ import annotations

import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..intake import normalize_intent
from ..project import resolve_context
from ..routing import AGENT_PROHIBITED_ACTIONS, WorkRoute
from .authorization import _authorization_ledger_digest
from .common import _write_json
from .execution import (
    EXECUTION_ENVELOPE_DEFAULT_HOURS,
    _execution_envelope_approval_digest,
)


def _validated_title(value: str) -> str:
    title = value.strip()
    if not title or not re.search(r"\w", title, flags=re.UNICODE):
        raise ValueError("title must contain at least one letter or number")
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
            raise ValueError(
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
        },
    )
    _write_json(unit_dir / "context-receipt.json", receipt)
    unit_dir.rename(final_unit_dir)
    staging.cleanup()
    return final_unit_dir
