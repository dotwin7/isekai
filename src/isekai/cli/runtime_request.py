from __future__ import annotations

import argparse
import json
from typing import Any


def runtime_request(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    """Translate parsed runtime arguments into the host-neutral request contract."""
    action = str(args.runtime_command)
    if action == "init":
        payload = {
            "path": args.path,
            "project_id": args.project_id,
            "foundation_path": args.foundation_path,
            "profiles": args.profile,
            "document_language": args.document_language,
            "maximum_agent_level": args.maximum_agent_level,
        }
    elif action == "handshake":
        payload = {
            "runtime": args.runtime,
            "adapter_version": args.adapter_version,
            "protocol_version": args.protocol_version,
            "project": args.project,
        }
    elif action == "on":
        payload = {"project": args.project}
    elif action in {"status", "resume"}:
        payload = {"project": args.project, "unit": args.unit}
    elif action == "unit-migrate":
        payload = {"project": args.project, "unit": args.unit}
    elif action in {"off", "compatibility"}:
        payload = {}
    elif action == "catalog-status":
        payload = {}
    elif action == "intake":
        payload = {
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
    elif action == "inception":
        payload = {"project": args.project}
    elif action == "release-check":
        payload = {"foundation": args.foundation}
    elif action == "foundation-decision":
        payload = {
            "foundation": args.foundation,
            "outcome": args.outcome,
            "summary": args.summary,
            "decided_by": args.decided_by,
        }
    elif action == "foundation-evidence":
        payload = {
            "foundation": args.foundation,
            "passed": args.passed,
            "checks": json.loads(args.checks_json),
            "scope": args.scope,
            "recorded_by": args.recorded_by,
        }
    elif action == "foundation-promote":
        payload = {"foundation": args.foundation}
    elif action == "project-knowledge-status":
        payload = {"project": args.project}
    elif action == "project-knowledge-propose":
        payload = {
            "unit": args.unit,
            "entries": json.loads(args.entries_json),
            "proposed_by": args.proposed_by,
        }
    elif action == "project-knowledge-promote":
        payload = {"unit": args.unit, "candidate": args.candidate}
    elif action == "route":
        payload = {
            "project": args.project,
            "change": args.change,
            "risk": args.risk,
            "ambiguous": args.ambiguous,
            "multi_party": args.multi_party,
            "remote": args.remote,
            "sensitive": args.sensitive,
        }
    elif action == "unit-init":
        payload = {
            "project": args.project,
            "title": args.title,
            "output": args.output,
            "owner": args.owner,
            "intent": json.loads(args.intent_json) if args.intent_json else None,
        }
    elif action == "checkpoint":
        payload = {
            "unit": args.unit,
            "completed": args.completed,
            "pending": args.pending,
            "blocked_by": args.blocked_by,
            "next_action": args.next_action,
        }
    elif action == "amend":
        payload = {
            "unit": args.unit,
            "request": args.request,
            "reason": args.reason,
            "affected_artifacts": args.affected_artifacts,
            "requested_by": args.requested_by,
        }
    elif action == "active-unit-detach":
        payload = {
            "project": args.project,
            "unit": args.unit,
            "requested_by": args.requested_by,
            "reason": args.reason,
        }
    elif action == "envelope-propose":
        payload = {
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
    elif action == "envelope-approve":
        payload = {"unit": args.unit}
    elif action == "authorize":
        payload = {
            "unit": args.unit,
            "requested_action": args.requested_action,
            "target": args.target,
            "stage": args.stage,
            "method": args.method,
            "credential_ref": args.credential_ref,
        }
    elif action == "managed-edit":
        payload = {
            "unit": args.unit,
            "changes": json.loads(args.changes_json),
        }
    elif action == "artifact-write":
        payload = {
            "unit": args.unit,
            "artifacts": json.loads(args.artifacts_json),
        }
    elif action == "prove":
        payload = {
            "unit": args.unit,
            "target": args.target,
            "command": json.loads(args.command_json),
            "timeout_seconds": args.timeout_seconds,
        }
    elif action == "evidence":
        payload = {
            "unit": args.unit,
            "passed": args.passed,
            "commands": json.loads(args.commands_json),
            "scope": args.scope,
            "recorded_by": args.recorded_by,
            "notes": args.notes,
        }
    elif action == "decision":
        payload = {
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
    elif action == "transition":
        payload = {"unit": args.unit, "to": args.to}
    elif action == "verify":
        payload = {"unit": args.unit}
    else:  # pragma: no cover - argparse restricts this value
        raise ValueError(f"unsupported runtime command: {action}")
    return action, payload


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
