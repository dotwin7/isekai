from __future__ import annotations

import argparse
import json
from typing import Any


def _project_request(
    action: str,
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    if action == "init":
        return {
            "path": args.path,
            "project_id": args.project_id,
            "foundation_path": args.foundation_path,
            "profiles": args.profile,
            "document_language": args.document_language,
            "maximum_agent_level": args.maximum_agent_level,
        }
    if action == "handshake":
        return {
            "runtime": args.runtime,
            "adapter_version": args.adapter_version,
            "protocol_version": args.protocol_version,
            "project": args.project,
        }
    if action in {"on", "inception", "project-knowledge-status"}:
        return {"project": args.project}
    if action in {"status", "resume", "unit-migrate"}:
        return {"project": args.project, "unit": args.unit}
    if action in {"off", "compatibility", "catalog-status"}:
        return {}
    if action == "intake":
        return {
            "project": args.project,
            "source": args.source,
            "goal": args.goal,
            "expected_outcome": args.expected_outcome,
            "scope": args.scope,
            "constraints": args.constraint,
            "acceptance_criteria": args.acceptance_criteria,
            "change": args.change,
            "risk": args.risk,
            "ambiguous": args.ambiguous,
            "multi_party": args.multi_party,
            "remote": args.remote,
            "sensitive": args.sensitive,
        }
    if action == "route":
        return {
            "project": args.project,
            "change": args.change,
            "risk": args.risk,
            "ambiguous": args.ambiguous,
            "multi_party": args.multi_party,
            "remote": args.remote,
            "sensitive": args.sensitive,
        }
    return None


def _foundation_request(
    action: str,
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    if action == "release-check":
        return {"foundation": args.foundation}
    if action == "foundation-decision":
        return {
            "foundation": args.foundation,
            "outcome": args.outcome,
            "summary": args.summary,
            "decided_by": args.decided_by,
        }
    if action == "foundation-evidence":
        return {
            "foundation": args.foundation,
            "passed": args.passed,
            "checks": json.loads(args.checks_json),
            "scope": args.scope,
            "recorded_by": args.recorded_by,
        }
    if action == "foundation-promote":
        return {"foundation": args.foundation}
    if action == "project-knowledge-propose":
        return {
            "unit": args.unit,
            "entries": json.loads(args.entries_json),
            "proposed_by": args.proposed_by,
        }
    if action == "project-knowledge-promote":
        return {"unit": args.unit, "candidate": args.candidate}
    return None


def _unit_request(
    action: str,
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    if action == "unit-init":
        return {
            "project": args.project,
            "title": args.title,
            "output": args.output,
            "owner": args.owner,
            "intent": json.loads(args.intent_json) if args.intent_json else None,
        }
    if action == "checkpoint":
        return {
            "unit": args.unit,
            "completed": args.completed,
            "pending": args.pending,
            "blocked_by": args.blocked_by,
            "next_action": args.next_action,
        }
    if action == "amend":
        return {
            "unit": args.unit,
            "request": args.request,
            "reason": args.reason,
            "affected_artifacts": args.affected_artifacts,
            "requested_by": args.requested_by,
        }
    if action == "active-unit-detach":
        return {
            "project": args.project,
            "unit": args.unit,
            "requested_by": args.requested_by,
            "reason": args.reason,
        }
    if action in {"envelope-approve", "verify"}:
        return {"unit": args.unit}
    if action == "transition":
        return {"unit": args.unit, "to": args.to}
    return None


def _execution_request(
    action: str,
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    if action == "envelope-propose":
        return {
            "unit": args.unit,
            "scope": args.scope,
            "stages": json.loads(args.stages_json),
            "allowed_actions": args.allowed_action,
            "forbidden_actions": args.forbidden_action,
            "external_access": json.loads(args.external_access_json),
            "max_iterations": args.max_iterations,
            "proposed_by": args.proposed_by,
            "expires_in_hours": args.expires_in_hours,
        }
    if action == "authorize":
        return {
            "unit": args.unit,
            "requested_action": args.requested_action,
            "target": args.target,
            "stage": args.stage,
            "method": args.method,
            "credential_ref": args.credential_ref,
        }
    if action == "managed-edit":
        return {"unit": args.unit, "changes": json.loads(args.changes_json)}
    if action == "artifact-write":
        return {"unit": args.unit, "artifacts": json.loads(args.artifacts_json)}
    if action == "prove":
        return {
            "unit": args.unit,
            "target": args.target,
            "command": json.loads(args.command_json),
            "timeout_seconds": args.timeout_seconds,
        }
    if action == "evidence":
        return {
            "unit": args.unit,
            "passed": args.passed,
            "commands": json.loads(args.commands_json),
            "scope": args.scope,
            "recorded_by": args.recorded_by,
            "notes": args.notes,
        }
    if action == "decision":
        return {
            "unit": args.unit,
            "gate": args.gate,
            "outcome": args.outcome,
            "summary": args.summary,
            "rationale": args.rationale,
            "alternatives": json.loads(args.alternatives_json),
            "tradeoffs": args.tradeoff,
            "risks": args.risk,
            "references": args.reference,
            "decided_by": args.decided_by,
        }
    return None


def runtime_request(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    """Translate parsed runtime arguments into the host-neutral request contract."""
    action = str(args.runtime_command)
    for builder in (
        _project_request,
        _foundation_request,
        _unit_request,
        _execution_request,
    ):
        payload = builder(action, args)
        if payload is not None:
            return action, payload
    raise ValueError(f"unsupported runtime command: {action}")


def runtime_exit_code(action: str, result: dict[str, Any]) -> int:
    values = result["result"]
    gates = {
        "authorize": "allowed",
        "managed-edit": "allowed",
        "prove": "allowed",
        "verify": "valid",
        "release-check": "ready",
        "foundation-promote": "promoted",
        "project-knowledge-promote": "promoted",
    }
    field = gates.get(action)
    return 0 if field is None or values[field] else 1
