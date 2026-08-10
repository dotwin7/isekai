from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from isekai.support.errors import IntegrityError
from .checkpointing import (
    authorization_cursor_issues,
    authorization_progress_cursor,
    checkpoint_progress_issues,
)
from .common import _unit_json, _unit_text


ARTIFACT_PLACEHOLDER_MARKER = "<!-- ISEKAI:placeholder -->"
ARTIFACT_SNAPSHOT_VERSION = "1.1.0"
SUPPORTED_ARTIFACT_SNAPSHOT_VERSIONS = {"1.0.0", ARTIFACT_SNAPSHOT_VERSION}

_TEMPLATE_BODIES = {
    "ko": {
        "requirements.md": "요구사항과 명시적 비목표를 기록합니다.",
        "architecture.md": "승인된 아키텍처와 외부 계약을 기록합니다.",
        "plan.md": "구현·검증 계획을 기록합니다.",
        "acceptance.md": "- [ ] 검증 가능한 인수 조건을 정의합니다.",
        "release.md": "릴리스 결정·근거·Rollback 계획을 기록합니다.",
        "operations.md": "배포 결과·운영 피드백·후속 작업을 기록합니다.",
        "implementation-guide.md": (
            "이 문서는 코드의 동작 방식·작성 표준·사용 예제·한계를 설명합니다. "
            "시스템 구조와 설계 선택의 근거는 architecture.md에 기록합니다.\n\n"
            "## 코드 표준\n\n"
            "- 사용하는 언어·프레임워크·테스트 표준을 기록합니다.\n\n"
            "## 동작 흐름\n\n"
            "- 주요 등록·조회·변경 흐름을 단계별로 설명합니다.\n\n"
            "## 오류와 검증\n\n"
            "- 실패 조건과 검증 방법을 기록합니다.\n\n"
            "## 사용 예제\n\n"
            "- 실제 호출·사용 예제를 기록합니다.\n\n"
            "## 한계와 확장\n\n"
            "- 현재 구현의 한계와 다음 확장 방향을 기록합니다."
        ),
    },
    "en": {
        "requirements.md": "Document the requirements and explicit non-goals.",
        "architecture.md": "Document the approved architecture and external contracts.",
        "plan.md": "Document the construction and validation plan.",
        "acceptance.md": "- [ ] Define verifiable acceptance criteria.",
        "release.md": "Record the release decision, evidence, and rollback plan.",
        "operations.md": "Record deployment, operational feedback, and follow-up work.",
        "implementation-guide.md": (
            "Explain code behavior, coding conventions, usage examples, and limitations. "
            "Record system structure and design rationale in architecture.md.\n\n"
            "## Coding standard\n\n"
            "- Record the language, framework, and test standards.\n\n"
            "## Behavior flow\n\n"
            "- Explain the main registration, lookup, and change flows.\n\n"
            "## Errors and verification\n\n"
            "- Record failure conditions and verification methods.\n\n"
            "## Usage example\n\n"
            "- Record practical invocation and usage examples.\n\n"
            "## Limitations and extensions\n\n"
            "- Record current limitations and next extension directions."
        ),
    },
}

_TEMPLATE_HEADINGS = {
    "ko": {
        "requirements.md": "# 요구사항",
        "architecture.md": "# 아키텍처",
        "plan.md": "# 계획",
        "acceptance.md": "# 인수 조건",
        "release.md": "# 릴리스",
        "operations.md": "# 운영",
        "implementation-guide.md": "# 구현 가이드",
    },
    "en": {
        "requirements.md": "# Requirements",
        "architecture.md": "# Architecture",
        "plan.md": "# Plan",
        "acceptance.md": "# Acceptance Criteria",
        "release.md": "# Release",
        "operations.md": "# Operations",
        "implementation-guide.md": "# Implementation Guide",
    },
}


def unit_document_templates(document_language: str) -> dict[str, str]:
    headings = _TEMPLATE_HEADINGS[document_language]
    return {
        relative: (
            f"{heading}\n\n{ARTIFACT_PLACEHOLDER_MARKER}\n\n{_TEMPLATE_BODIES[document_language][relative]}\n"
        )
        for relative, heading in headings.items()
    }


_READINESS_STAGE_ARTIFACTS = {
    "inception": (
        "intent.md",
        "requirements.md",
        "plan.md",
        "acceptance.md",
        "release.md",
        "operations.md",
    ),
    "construction": ("architecture.md", "implementation-guide.md"),
    "release": (),
    "operations": (),
}
_GATE_ARTIFACTS = {
    "inception": ("intent.md", "requirements.md", "plan.md", "acceptance.md"),
    "architecture": ("architecture.md", "implementation-guide.md"),
    "release": ("release.md",),
    "operation": ("operations.md",),
}
_STAGE_ORDER = ("inception", "construction", "release", "operations")
_GATE_STAGE = {
    "inception": "inception",
    "architecture": "construction",
    "release": "release",
    "operation": "operations",
}
_GATE_PROGRESS_STAGES = {
    "inception": frozenset(),
    "architecture": frozenset({"construction"}),
    "release": frozenset({"construction", "validation", "release"}),
    "operation": frozenset(
        {"construction", "validation", "release", "operations"}
    ),
}
_TARGET_STAGE = {
    "inception": "inception",
    "awaiting-inception-decision": "inception",
    "construction": "inception",
    "validation": "construction",
    "awaiting-release-decision": "construction",
    "releasing": "release",
    "operating": "release",
    "learned": "operations",
}
_STATUS_STAGE = {
    "proposed": "inception",
    "inception": "inception",
    "awaiting-inception-decision": "inception",
    "construction": "inception",
    "validation": "construction",
    "awaiting-release-decision": "construction",
    "releasing": "release",
    "operating": "operations",
    "learned": "operations",
}
_INTENT_PLACEHOLDERS = {
    "기대 결과를 정의합니다.",
    "작업 범위를 정의합니다.",
    "제약사항을 정의합니다.",
    "검증 가능한 인수 조건을 정의합니다.",
    "Define the expected outcome.",
    "Define the work scope.",
    "Define constraints.",
    "Define verifiable acceptance criteria.",
}
_PLAN_STAGES = ("inception", "construction", "validation", "release", "operations", "learn")
_CHECKBOX = re.compile(r"(?P<prefix>^[ \t]*[-*+][ \t]+\[)[ xX]*(?P<suffix>\])", re.MULTILINE)


def _required_artifacts(stage: str) -> tuple[str, ...]:
    if stage not in _STAGE_ORDER:
        raise ValueError(f"unsupported artifact readiness stage: {stage}")
    stage_index = _STAGE_ORDER.index(stage)
    return tuple(
        relative
        for included_stage in _STAGE_ORDER[: stage_index + 1]
        for relative in _READINESS_STAGE_ARTIFACTS[included_stage]
    )


def _legacy_template(document_language: str, relative: str) -> str:
    return (
        f"{_TEMPLATE_HEADINGS[document_language][relative]}\n\n"
        f"{_TEMPLATE_BODIES[document_language][relative]}\n"
    )


def _markdown_readiness_issues(
    relative: str,
    content: str,
    document_language: str,
) -> list[str]:
    issues: list[str] = []
    if ARTIFACT_PLACEHOLDER_MARKER in content:
        issues.append(f"{relative} still contains the ISEKAI placeholder marker")
    if relative in _TEMPLATE_BODIES.get(document_language, {}):
        if content.strip() == _legacy_template(document_language, relative).strip():
            issues.append(f"{relative} still contains only the initialization template")
    if relative == "intent.md":
        for placeholder in sorted(_INTENT_PLACEHOLDERS):
            if placeholder in content:
                issues.append(f"intent.md still contains placeholder content: {placeholder}")
    if relative == "requirements.md":
        non_goal_tokens = ("비목표", "non-goal", "non goal")
        if not any(token in content.casefold() for token in non_goal_tokens):
            issues.append("requirements.md must record explicit non-goals")
    if relative == "plan.md":
        lowered = content.casefold()
        missing_stages = [stage for stage in _PLAN_STAGES if stage not in lowered]
        if missing_stages:
            issues.append(
                "plan.md must record every lifecycle stage: " + ", ".join(missing_stages)
            )
    if relative == "acceptance.md":
        criteria = list(_CHECKBOX.finditer(content))
        if not criteria:
            issues.append("acceptance.md must define at least one checkable criterion")
    return issues


def artifact_readiness_issues(unit_dir: Path, stage: str) -> list[str]:
    try:
        unit = _unit_json(unit_dir, "unit.json")
    except IntegrityError as exc:
        return [str(exc)]
    document_language = str(unit.get("document_language"))
    issues: list[str] = []
    for relative in _required_artifacts(stage):
        try:
            content = _unit_text(unit_dir, relative)
        except IntegrityError as exc:
            issues.append(str(exc))
            continue
        issues.extend(_markdown_readiness_issues(relative, content, document_language))
    return list(dict.fromkeys(issues))


def target_artifact_readiness_issues(unit_dir: Path, target_status: str) -> list[str]:
    stage = _TARGET_STAGE.get(target_status)
    return artifact_readiness_issues(unit_dir, stage) if stage is not None else []


def status_artifact_readiness_issues(unit_dir: Path, status: str) -> list[str]:
    stage = _STATUS_STAGE.get(status)
    return artifact_readiness_issues(unit_dir, stage) if stage is not None else []


def gate_artifact_readiness_issues(unit_dir: Path, gate: str) -> list[str]:
    stage = _GATE_STAGE.get(gate)
    if stage is None:
        return []
    return [
        *artifact_readiness_issues(unit_dir, stage),
        *checkpoint_progress_issues(unit_dir),
    ]


def _snapshot_artifact_bytes(unit_dir: Path, relative: str) -> bytes:
    content = _unit_text(unit_dir, relative)
    if relative == "acceptance.md":
        content = _CHECKBOX.sub(r"\g<prefix> \g<suffix>", content)
    return content.replace("\r\n", "\n").encode("utf-8")


def artifact_content_digest(unit_dir: Path, relative: str) -> str:
    return "sha256:" + hashlib.sha256(
        _snapshot_artifact_bytes(unit_dir, relative)
    ).hexdigest()


def _snapshot_digest(subject: dict[str, Any]) -> str:
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def build_artifact_snapshot(unit_dir: Path, gate: str) -> dict[str, Any] | None:
    stage = _GATE_STAGE.get(gate)
    if stage is None:
        return None
    references = _GATE_ARTIFACTS[gate]
    artifacts = [
        {
            "reference": relative,
            "digest": "sha256:" + hashlib.sha256(
                _snapshot_artifact_bytes(unit_dir, relative)
            ).hexdigest(),
        }
        for relative in references
    ]
    subject: dict[str, Any] = {
        "type": "unit-artifact-snapshot",
        "version": ARTIFACT_SNAPSHOT_VERSION,
        "gate": gate,
        "artifacts": artifacts,
        "authorization_cursor": authorization_progress_cursor(
            unit_dir,
            stages=_GATE_PROGRESS_STAGES[gate],
        ),
    }
    subject["digest"] = _snapshot_digest(subject)
    return subject


def artifact_snapshot_issues(
    snapshot: Any,
    gate: str,
    *,
    unit_dir: Path | None = None,
) -> list[str]:
    if not isinstance(snapshot, dict):
        return [f"approved {gate} Decision artifact_snapshot must be an object"]
    issues: list[str] = []
    if snapshot.get("type") != "unit-artifact-snapshot":
        issues.append(f"{gate} Decision artifact_snapshot has an invalid type")
    version = snapshot.get("version")
    if version not in SUPPORTED_ARTIFACT_SNAPSHOT_VERSIONS:
        issues.append(f"{gate} Decision artifact_snapshot has an unsupported version")
    if snapshot.get("gate") != gate:
        issues.append(f"{gate} Decision artifact_snapshot gate does not match")
    artifacts = snapshot.get("artifacts")
    stage = _GATE_STAGE.get(gate)
    expected_references = _GATE_ARTIFACTS.get(gate, ()) if stage is not None else ()
    if not isinstance(artifacts, list):
        issues.append(f"{gate} Decision artifact_snapshot artifacts must be a list")
        artifacts = []
    references: list[str] = []
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            issues.append(f"{gate} Decision artifact_snapshot item {index} must be an object")
            continue
        reference = artifact.get("reference")
        digest = artifact.get("digest")
        if not isinstance(reference, str) or not reference.strip():
            issues.append(f"{gate} Decision artifact_snapshot item {index} needs reference")
            continue
        references.append(reference)
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            issues.append(f"{gate} Decision artifact_snapshot item {index} needs SHA-256 digest")
            continue
        if unit_dir is not None:
            try:
                current_digest = "sha256:" + hashlib.sha256(
                    _snapshot_artifact_bytes(unit_dir, reference)
                ).hexdigest()
            except IntegrityError as exc:
                issues.append(str(exc))
            else:
                if digest != current_digest:
                    issues.append(
                        f"{reference} changed after the approved {gate} Decision"
                    )
    if tuple(references) != tuple(expected_references):
        issues.append(
            f"{gate} Decision artifact_snapshot must contain: "
            + ", ".join(expected_references)
        )
    if version == ARTIFACT_SNAPSHOT_VERSION:
        cursor = snapshot.get("authorization_cursor")
        issues.extend(
            f"{gate} Decision artifact_snapshot {issue}"
            for issue in authorization_cursor_issues(cursor)
        )
        if unit_dir is not None and isinstance(cursor, dict):
            try:
                current_cursor = authorization_progress_cursor(
                    unit_dir,
                    stages=_GATE_PROGRESS_STAGES[gate],
                )
            except IntegrityError as exc:
                issues.append(str(exc))
            else:
                if cursor != current_cursor:
                    issues.append(
                        f"authorized implementation progress changed after the "
                        f"approved {gate} Decision"
                    )
    digest = snapshot.get("digest")
    digest_subject = {key: value for key, value in snapshot.items() if key != "digest"}
    if not isinstance(digest, str) or digest != _snapshot_digest(digest_subject):
        issues.append(f"{gate} Decision artifact_snapshot digest does not match")
    return issues


def latest_decision_artifact_issues(
    unit_dir: Path,
    decisions: dict[str, Any],
    gate: str,
) -> list[str]:
    entries = decisions.get("decisions")
    if not isinstance(entries, list):
        return []
    latest = next(
        (
            decision
            for decision in reversed(entries)
            if isinstance(decision, dict) and decision.get("gate") == gate
        ),
        None,
    )
    if latest is None or latest.get("outcome") != "approved":
        return []
    snapshot = latest.get("artifact_snapshot")
    if snapshot is None:
        return []  # Legacy Decisions predate artifact snapshots.
    return artifact_snapshot_issues(snapshot, gate, unit_dir=unit_dir)


def approved_artifact_snapshot_issues(
    unit_dir: Path,
    decisions: dict[str, Any],
) -> list[str]:
    return [
        issue
        for gate in _GATE_STAGE
        for issue in latest_decision_artifact_issues(unit_dir, decisions, gate)
    ]
