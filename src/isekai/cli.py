from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .distribution import (
    MANIFEST_PATH,
    doctor_install,
    install_from_bootstrap_checkout,
    install_from_git,
    load_install_lock,
    plan_git_update,
    rollback_install,
    verify_distribution,
    write_distribution_manifest,
)
from .foundation import FoundationError, load_foundation
from .plugin_contract import dispatch
from .workflow import (
    EXECUTION_ENVELOPE_DEFAULT_HOURS,
    EXECUTION_ENVELOPE_MAX_HOURS,
    WorkRoute,
    resolve_context,
    unit_status,
    verify_unit,
)


def _json(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


# Typing one of these as the first argument runs the plugin action of the same
# name, so no top-level command may reuse one: the alias would shadow it and
# leave the top-level parser unreachable.
DIRECT_PLUGIN_ACTIONS = {
    "init",
    "handshake",
    "on",
    "off",
    "status",
    "intake",
    "route",
    "inception",
    "compatibility",
    "release-check",
    "foundation-decision",
    "foundation-evidence",
    "foundation-promote",
    "resume",
    "unit-init",
    "checkpoint",
    "envelope-propose",
    "envelope-approve",
    "authorize",
    "evidence",
    "decision",
    "transition",
    "verify",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m isekai")
    commands = parser.add_subparsers(dest="command", required=True)

    install = commands.add_parser(
        "install", help="install a pinned Git release into a project"
    )
    install.add_argument("--source", required=True)
    install.add_argument("--ref", required=True)
    install.add_argument("--path", default=".")
    install.add_argument(
        "--runtime",
        action="append",
        choices=("all", "kiro", "claude", "codex"),
        default=[],
    )
    install.add_argument("--adopt-foundation", action="store_true")
    install.add_argument("--register", action="store_true")
    install.add_argument(
        "--checkout",
        help=(
            "install from a Git checkout the bootstrap script already resolved "
            "instead of cloning again; the checkout must have --ref checked out"
        ),
    )

    update = commands.add_parser(
        "update", help="update Core and adapters from a new immutable Git ref"
    )
    update.add_argument("--source")
    update.add_argument("--ref", required=True)
    update.add_argument("--path", default=".")
    update.add_argument(
        "--runtime",
        action="append",
        choices=("all", "kiro", "claude", "codex"),
        default=[],
    )
    update.add_argument("--include-foundation", action="store_true")
    update.add_argument("--adopt-foundation", action="store_true")
    update.add_argument("--register", action="store_true")
    update.add_argument("--check", action="store_true")

    doctor = commands.add_parser(
        "doctor", help="verify project lock, installed files, and Foundation pin"
    )
    doctor.add_argument("--path", default=".")

    rollback = commands.add_parser(
        "rollback", help="restore the previous project-local ISEKAI installation"
    )
    rollback.add_argument("--path", default=".")
    rollback.add_argument("--register", action="store_true")

    distribution_build = commands.add_parser(
        "distribution-build", help="write a digest-pinned Git release manifest"
    )
    distribution_build.add_argument("--root", default=".")
    distribution_build.add_argument("--output", default=str(MANIFEST_PATH))

    distribution_check = commands.add_parser(
        "distribution-check", help="verify the checked-in release manifest"
    )
    distribution_check.add_argument("--root", default=".")

    validate = commands.add_parser("validate", help="validate a Foundation release")
    validate.add_argument("--foundation", default="foundation")

    resolve = commands.add_parser("resolve", help="resolve a project Context Receipt")
    resolve.add_argument("project")
    resolve.add_argument("--route", choices=tuple(item.value for item in WorkRoute), default="unit")

    status = commands.add_parser("unit-status", help="show Unit lifecycle and artifact status")
    status.add_argument("unit_dir")

    verify = commands.add_parser("unit-verify", help="verify a complete AI-DLC Unit")
    verify.add_argument("unit_dir")

    structure = commands.add_parser("structure", help="list prototype files")
    structure.add_argument("--root", default=".")

    plugin = commands.add_parser("plugin", help="run the ISEKAI agent plugin contract")
    plugin_commands = plugin.add_subparsers(dest="plugin_command", required=True)

    plugin_project_init = plugin_commands.add_parser(
        "init", help="initialize an ISEKAI project manifest and Unit root"
    )
    plugin_project_init.add_argument("--path", default=".")
    plugin_project_init.add_argument("--id", dest="project_id")
    plugin_project_init.add_argument("--foundation-path")
    plugin_project_init.add_argument("--profile", action="append", default=[])
    plugin_project_init.add_argument(
        "--document-language", choices=("ko", "en"), default="ko"
    )
    plugin_project_init.add_argument("--maximum-agent-level", default="L0")

    plugin_handshake = plugin_commands.add_parser(
        "handshake", help="verify Adapter, Core, protocol, and project lock compatibility"
    )
    plugin_handshake.add_argument("--runtime", choices=("kiro", "claude", "codex"), required=True)
    plugin_handshake.add_argument("--adapter-version", required=True)
    plugin_handshake.add_argument("--protocol-version", required=True)
    plugin_handshake.add_argument("--project", default=".")

    plugin_on = plugin_commands.add_parser(
        "on", help="activate ISEKAI mode for the current conversation"
    )
    plugin_on.add_argument("--project", default=".")

    plugin_commands.add_parser(
        "off", help="deactivate conversation-local ISEKAI mode without writing artifacts"
    )

    plugin_intake = plugin_commands.add_parser(
        "intake", help="normalize a Goal or direct request and route it"
    )
    plugin_intake.add_argument("--source", choices=("host-goal", "direct-request"), default="direct-request")
    plugin_intake.add_argument("--goal", required=True)
    plugin_intake.add_argument("--expected-outcome", dest="expected_outcome", default="")
    plugin_intake.add_argument("--scope", action="append", default=[])
    plugin_intake.add_argument("--constraint", action="append", default=[])
    plugin_intake.add_argument("--acceptance-criterion", dest="acceptance_criteria", action="append", default=[])
    plugin_intake.add_argument("--change", choices=("none", "local", "persistent"))
    plugin_intake.add_argument("--risk", choices=("low", "high"), default="low")
    plugin_intake.add_argument("--ambiguous", action="store_true")
    plugin_intake.add_argument("--multi-party", dest="multi_party", action="store_true")
    plugin_intake.add_argument("--remote", action="store_true")
    plugin_intake.add_argument("--sensitive", action="store_true")

    plugin_status = plugin_commands.add_parser("status", help="show project and Unit context")
    plugin_status.add_argument("--project", default=".")
    plugin_status.add_argument("--unit")

    plugin_route = plugin_commands.add_parser("route", help="route work through ISEKAI")
    plugin_route.add_argument("--change", choices=("none", "local", "persistent"), required=True)
    plugin_route.add_argument("--risk", choices=("low", "high"), default="low")
    plugin_route.add_argument("--ambiguous", action="store_true")
    plugin_route.add_argument("--multi-party", dest="multi_party", action="store_true")
    plugin_route.add_argument("--remote", action="store_true")
    plugin_route.add_argument("--sensitive", action="store_true")

    plugin_inception = plugin_commands.add_parser("inception", help="prepare inception questions")
    plugin_inception.add_argument("--project", default=".")

    plugin_compatibility = plugin_commands.add_parser(
        "compatibility", help="show runtime CLI compatibility matrix"
    )

    plugin_release_check = plugin_commands.add_parser(
        "release-check", help="report Foundation release-readiness blockers"
    )
    plugin_release_check.add_argument("--foundation", default="foundation")

    plugin_foundation_decision = plugin_commands.add_parser(
        "foundation-decision", help="record a Foundation release Decision"
    )
    plugin_foundation_decision.add_argument("--foundation", default="foundation")
    plugin_foundation_decision.add_argument(
        "--outcome", choices=("approved", "rejected"), required=True
    )
    plugin_foundation_decision.add_argument("--summary", required=True)
    plugin_foundation_decision.add_argument("--decided-by", dest="decided_by", required=True)

    plugin_foundation_evidence = plugin_commands.add_parser(
        "foundation-evidence", help="record Foundation release Evidence"
    )
    plugin_foundation_evidence.add_argument("--foundation", default="foundation")
    plugin_foundation_evidence.add_argument("--passed", action="store_true")
    plugin_foundation_evidence.add_argument("--checks-json", required=True)
    plugin_foundation_evidence.add_argument("--scope", required=True)
    plugin_foundation_evidence.add_argument("--recorded-by", dest="recorded_by", required=True)

    plugin_foundation_promote = plugin_commands.add_parser(
        "foundation-promote", help="promote Foundation after approval gates pass"
    )
    plugin_foundation_promote.add_argument("--foundation", default="foundation")

    plugin_resume = plugin_commands.add_parser("resume", help="restore Unit checkpoint context")
    plugin_resume.add_argument("--project", default=".")
    plugin_resume.add_argument("--unit")

    plugin_init = plugin_commands.add_parser("unit-init", help="create a Unit through the plugin contract")
    plugin_init.add_argument("--project", required=True)
    plugin_init.add_argument("--title", required=True)
    plugin_init.add_argument("--output")
    plugin_init.add_argument("--owner", default="unassigned")
    plugin_init.add_argument("--intent-json", default="")

    plugin_checkpoint = plugin_commands.add_parser("checkpoint", help="write an explicit Unit checkpoint")
    plugin_checkpoint.add_argument("--unit", required=True)
    plugin_checkpoint.add_argument("--completed", action="append", default=[])
    plugin_checkpoint.add_argument("--pending", action="append", default=[])
    plugin_checkpoint.add_argument("--blocked-by", dest="blocked_by", action="append", default=[])
    plugin_checkpoint.add_argument("--next-action", required=True)

    plugin_envelope_propose = plugin_commands.add_parser(
        "envelope-propose", help="propose an adaptive Execution Envelope for a Unit"
    )
    plugin_envelope_propose.add_argument("--unit", required=True)
    plugin_envelope_propose.add_argument("--scope", action="append", required=True)
    plugin_envelope_propose.add_argument("--stages-json", required=True)
    plugin_envelope_propose.add_argument("--allowed-action", action="append", required=True)
    plugin_envelope_propose.add_argument("--forbidden-action", action="append", default=[])
    plugin_envelope_propose.add_argument("--max-iterations", type=int, required=True)
    plugin_envelope_propose.add_argument("--proposed-by", required=True)
    plugin_envelope_propose.add_argument(
        "--expires-in-hours",
        dest="expires_in_hours",
        type=int,
        default=EXECUTION_ENVELOPE_DEFAULT_HOURS,
        help=(
            "approval window in hours "
            f"(default {EXECUTION_ENVELOPE_DEFAULT_HOURS}, "
            f"maximum {EXECUTION_ENVELOPE_MAX_HOURS})"
        ),
    )

    plugin_envelope_approve = plugin_commands.add_parser(
        "envelope-approve",
        help="activate a proposed Execution Envelope from its approved inception Decision",
    )
    plugin_envelope_approve.add_argument("--unit", required=True)

    plugin_authorize = plugin_commands.add_parser(
        "authorize", help="check an action against the approved Execution Envelope"
    )
    plugin_authorize.add_argument("--unit", required=True)
    plugin_authorize.add_argument("--action", dest="requested_action", required=True)
    plugin_authorize.add_argument("--target", required=True)
    plugin_authorize.add_argument("--stage")

    plugin_evidence = plugin_commands.add_parser("evidence", help="record structured verification Evidence")
    plugin_evidence.add_argument("--unit", required=True)
    plugin_evidence.add_argument("--passed", action="store_true")
    plugin_evidence.add_argument("--commands-json", required=True)
    plugin_evidence.add_argument("--scope", required=True)
    plugin_evidence.add_argument("--recorded-by", dest="recorded_by", required=True)
    plugin_evidence.add_argument("--notes", default="")

    plugin_decision = plugin_commands.add_parser("decision", help="record a human Unit Decision")
    plugin_decision.add_argument("--unit", required=True)
    plugin_decision.add_argument(
        "--gate",
        choices=("inception", "architecture", "release", "operation", "knowledge"),
        required=True,
    )
    plugin_decision.add_argument("--outcome", choices=("approved", "rejected"), required=True)
    plugin_decision.add_argument("--summary", required=True)
    plugin_decision.add_argument("--rationale", action="append", required=True)
    plugin_decision.add_argument("--alternatives-json", default="[]")
    plugin_decision.add_argument("--tradeoff", action="append", default=[])
    plugin_decision.add_argument("--risk", action="append", default=[])
    plugin_decision.add_argument("--reference", action="append", default=[])
    plugin_decision.add_argument("--decided-by", dest="decided_by", required=True)

    plugin_transition = plugin_commands.add_parser(
        "transition", help="transition a Unit through an allowed lifecycle edge"
    )
    plugin_transition.add_argument("--unit", required=True)
    plugin_transition.add_argument(
        "--to",
        choices=(
            "proposed",
            "inception",
            "awaiting-inception-decision",
            "construction",
            "awaiting-release-decision",
            "releasing",
            "operating",
            "learned",
        ),
        required=True,
    )

    plugin_verify = plugin_commands.add_parser("verify", help="verify a Unit through the plugin contract")
    plugin_verify.add_argument("--unit", required=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in DIRECT_PLUGIN_ACTIONS:
        arguments = ["plugin", *arguments]
    args = _parser().parse_args(arguments)
    try:
        if args.command == "install":
            if args.checkout:
                _json(
                    install_from_bootstrap_checkout(
                        args.checkout,
                        args.source,
                        args.ref,
                        args.path,
                        runtimes=args.runtime or ("all",),
                        adopt_foundation=args.adopt_foundation,
                        register=args.register,
                    )
                )
            else:
                _json(
                    install_from_git(
                        args.source,
                        args.ref,
                        args.path,
                        runtimes=args.runtime or ("all",),
                        adopt_foundation=args.adopt_foundation,
                        register=args.register,
                    )
                )
        elif args.command == "update":
            lock = load_install_lock(args.path)
            if lock is None:
                raise ValueError("cannot update before ISEKAI is installed")
            source = args.source or lock.get("source", {}).get("git")
            if not isinstance(source, str) or not source:
                raise ValueError("update requires --source or a Git source in isekai.lock.json")
            runtimes = args.runtime or tuple(lock.get("adapters", {}))
            if args.check:
                _json(
                    plan_git_update(
                        source,
                        args.ref,
                        args.path,
                        runtimes=runtimes,
                        include_foundation=args.include_foundation,
                    )
                )
            else:
                _json(
                    install_from_git(
                        source,
                        args.ref,
                        args.path,
                        runtimes=runtimes,
                        update=True,
                        include_foundation=args.include_foundation,
                        adopt_foundation=args.adopt_foundation,
                        register=args.register,
                    )
                )
        elif args.command == "doctor":
            result = doctor_install(args.path)
            _json(result)
            return 0 if result["ready"] else 1
        elif args.command == "rollback":
            _json(rollback_install(args.path, register=args.register))
        elif args.command == "distribution-build":
            path = write_distribution_manifest(args.root, args.output)
            _json({"created": str(path)})
        elif args.command == "distribution-check":
            result = verify_distribution(args.root)
            _json(result)
            return 0 if result["valid"] else 1
        elif args.command == "validate":
            _json({"valid": True, "foundation": load_foundation(args.foundation).summary()})
        elif args.command == "resolve":
            _json(resolve_context(args.project, WorkRoute(args.route)))
        elif args.command == "unit-status":
            _json(unit_status(args.unit_dir))
        elif args.command == "unit-verify":
            result = verify_unit(args.unit_dir)
            _json(result)
            return 0 if result["valid"] else 1
        elif args.command == "structure":
            root = Path(args.root).resolve()
            files = [
                str(path.relative_to(root))
                for base in ("foundation", "src", "tests")
                if (root / base).exists()
                for path in sorted((root / base).rglob("*"))
                if path.is_file() and "__pycache__" not in path.parts
            ]
            _json({"root": str(root), "files": files})
        elif args.command == "plugin":
            action = args.plugin_command
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
            elif action == "off":
                payload = {}
            elif action == "intake":
                payload = {
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
            elif action == "compatibility":
                payload = {}
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
            elif action == "route":
                payload = {
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
            elif action == "envelope-propose":
                payload = {
                    "unit": args.unit,
                    "scope": args.scope,
                    "stages": json.loads(args.stages_json),
                    "allowed_actions": args.allowed_action,
                    "forbidden_actions": args.forbidden_action,
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
                raise ValueError(f"unsupported plugin command: {action}")
            result = dispatch(action, payload)
            _json(result)
            if action == "authorize":
                return 0 if result["result"]["allowed"] else 1
            if action == "verify":
                return 0 if result["result"]["valid"] else 1
            if action == "release-check":
                return 0 if result["result"]["ready"] else 1
            if action == "foundation-promote":
                return 0 if result["result"]["promoted"] else 1
        return 0
    except (FoundationError, ValueError, FileExistsError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
