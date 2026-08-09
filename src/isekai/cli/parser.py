from __future__ import annotations

import argparse

from ..distribution import MANIFEST_PATH
from ..workflow import (
    EXECUTION_ENVELOPE_DEFAULT_HOURS,
    EXECUTION_ENVELOPE_MAX_HOURS,
    WorkRoute,
)


# Typing one of these as the first argument runs the runtime action of the same
# name, so no top-level command may reuse one: the alias would shadow it and
# leave the top-level parser unreachable.
DIRECT_RUNTIME_ACTIONS = {
    "init",
    "handshake",
    "on",
    "off",
    "status",
    "intake",
    "route",
    "inception",
    "compatibility",
    "feature-status",
    "release-check",
    "foundation-decision",
    "foundation-evidence",
    "foundation-promote",
    "project-knowledge-status",
    "project-knowledge-propose",
    "project-knowledge-promote",
    "resume",
    "unit-migrate",
    "unit-init",
    "checkpoint",
    "amend",
    "active-unit-detach",
    "envelope-propose",
    "envelope-approve",
    "authorize",
    "managed-edit",
    "artifact-write",
    "managed-test",
    "evidence",
    "decision",
    "transition",
    "verify",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m isekai")
    commands = parser.add_subparsers(dest="command", required=True)

    mcp_serve = commands.add_parser(
        "mcp-serve", help="serve the Project-scoped Core tool gateway over stdio"
    )
    mcp_serve.add_argument("--project", default=".")
    mcp_serve.add_argument(
        "--runtime", choices=("kiro", "claude", "codex"), required=True
    )

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
    update.add_argument("--check", action="store_true")

    doctor = commands.add_parser(
        "doctor",
        help="verify the project installation and Runtime execution guards",
    )
    doctor.add_argument("--path", default=".")
    doctor.add_argument(
        "--fix",
        action="store_true",
        help="restore Project-local Runtime execution guards",
    )

    rollback = commands.add_parser(
        "rollback", help="restore the previous project-local ISEKAI installation"
    )
    rollback.add_argument("--path", default=".")

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

    runtime = commands.add_parser(
        "runtime", help="run the ISEKAI project-local runtime contract"
    )
    runtime_commands = runtime.add_subparsers(dest="runtime_command", required=True)

    runtime_project_init = runtime_commands.add_parser(
        "init", help="initialize an ISEKAI project manifest and Unit root"
    )
    runtime_project_init.add_argument("--path", default=".")
    runtime_project_init.add_argument("--id", dest="project_id")
    runtime_project_init.add_argument("--foundation-path")
    runtime_project_init.add_argument("--profile", action="append", default=[])
    runtime_project_init.add_argument(
        "--document-language", choices=("ko", "en"), default="ko"
    )
    runtime_project_init.add_argument("--maximum-agent-level", default="L0")

    runtime_handshake = runtime_commands.add_parser(
        "handshake", help="verify Adapter, Core, protocol, and project lock compatibility"
    )
    runtime_handshake.add_argument("--runtime", choices=("kiro", "claude", "codex"), required=True)
    runtime_handshake.add_argument("--adapter-version", required=True)
    runtime_handshake.add_argument("--protocol-version", required=True)
    runtime_handshake.add_argument("--project", default=".")

    runtime_on = runtime_commands.add_parser(
        "on", help="activate ISEKAI mode for the current conversation"
    )
    runtime_on.add_argument("--project", default=".")

    runtime_commands.add_parser(
        "off", help="deactivate conversation-local ISEKAI mode without writing artifacts"
    )

    runtime_intake = runtime_commands.add_parser(
        "intake", help="normalize a Goal or direct request and route it"
    )
    runtime_intake.add_argument("--project", default=".")
    runtime_intake.add_argument("--source", choices=("host-goal", "direct-request"), default="direct-request")
    runtime_intake.add_argument("--goal", required=True)
    runtime_intake.add_argument("--expected-outcome", dest="expected_outcome", default="")
    runtime_intake.add_argument("--scope", action="append", default=[])
    runtime_intake.add_argument("--constraint", action="append", default=[])
    runtime_intake.add_argument("--acceptance-criterion", dest="acceptance_criteria", action="append", default=[])
    runtime_intake.add_argument("--change", choices=("none", "local", "persistent"))
    runtime_intake.add_argument("--risk", choices=("low", "high"), default="low")
    runtime_intake.add_argument("--ambiguous", action="store_true")
    runtime_intake.add_argument("--multi-party", dest="multi_party", action="store_true")
    runtime_intake.add_argument("--remote", action="store_true")
    runtime_intake.add_argument("--sensitive", action="store_true")

    runtime_status = runtime_commands.add_parser("status", help="show project and Unit context")
    runtime_status.add_argument("--project", default=".")
    runtime_status.add_argument("--unit")

    runtime_route = runtime_commands.add_parser("route", help="route work through ISEKAI")
    runtime_route.add_argument("--project", default=".")
    runtime_route.add_argument("--change", choices=("none", "local", "persistent"), required=True)
    runtime_route.add_argument("--risk", choices=("low", "high"), default="low")
    runtime_route.add_argument("--ambiguous", action="store_true")
    runtime_route.add_argument("--multi-party", dest="multi_party", action="store_true")
    runtime_route.add_argument("--remote", action="store_true")
    runtime_route.add_argument("--sensitive", action="store_true")

    runtime_inception = runtime_commands.add_parser("inception", help="prepare inception questions")
    runtime_inception.add_argument("--project", default=".")

    runtime_compatibility = runtime_commands.add_parser(
        "compatibility", help="show runtime CLI compatibility matrix"
    )

    runtime_commands.add_parser(
        "feature-status",
        help="show versioned features attached to this ISEKAI Runtime",
    )

    runtime_release_check = runtime_commands.add_parser(
        "release-check", help="report Foundation release-readiness blockers"
    )
    runtime_release_check.add_argument("--foundation", default="foundation")

    runtime_foundation_decision = runtime_commands.add_parser(
        "foundation-decision", help="record a Foundation release Decision"
    )
    runtime_foundation_decision.add_argument("--foundation", default="foundation")
    runtime_foundation_decision.add_argument(
        "--outcome", choices=("approved", "rejected"), required=True
    )
    runtime_foundation_decision.add_argument("--summary", required=True)
    runtime_foundation_decision.add_argument("--decided-by", dest="decided_by", required=True)

    runtime_foundation_evidence = runtime_commands.add_parser(
        "foundation-evidence", help="record Foundation release Evidence"
    )
    runtime_foundation_evidence.add_argument("--foundation", default="foundation")
    runtime_foundation_evidence.add_argument("--passed", action="store_true")
    runtime_foundation_evidence.add_argument("--checks-json", required=True)
    runtime_foundation_evidence.add_argument("--scope", required=True)
    runtime_foundation_evidence.add_argument("--recorded-by", dest="recorded_by", required=True)

    runtime_foundation_promote = runtime_commands.add_parser(
        "foundation-promote", help="promote Foundation after approval gates pass"
    )
    runtime_foundation_promote.add_argument("--foundation", default="foundation")

    runtime_project_knowledge_status = runtime_commands.add_parser(
        "project-knowledge-status",
        help="show the latest approved Project Knowledge release",
    )
    runtime_project_knowledge_status.add_argument("--project", default=".")

    runtime_project_knowledge_propose = runtime_commands.add_parser(
        "project-knowledge-propose",
        help="propose reusable knowledge from an operating or learned Unit",
    )
    runtime_project_knowledge_propose.add_argument("--unit", required=True)
    runtime_project_knowledge_propose.add_argument("--entries-json", required=True)
    runtime_project_knowledge_propose.add_argument(
        "--proposed-by", dest="proposed_by", required=True
    )

    runtime_project_knowledge_promote = runtime_commands.add_parser(
        "project-knowledge-promote",
        help="promote a candidate bound by an approved knowledge Decision",
    )
    runtime_project_knowledge_promote.add_argument("--unit", required=True)
    runtime_project_knowledge_promote.add_argument("--candidate", required=True)

    runtime_resume = runtime_commands.add_parser("resume", help="restore Unit checkpoint context")
    runtime_resume.add_argument("--project", default=".")
    runtime_resume.add_argument("--unit")

    runtime_unit_migrate = runtime_commands.add_parser(
        "unit-migrate",
        help="rebind a moved Unit to the same Project contract using portable paths",
    )
    runtime_unit_migrate.add_argument("--project", default=".")
    runtime_unit_migrate.add_argument("--unit", required=True)

    runtime_init = runtime_commands.add_parser("unit-init", help="create a Unit through the runtime contract")
    runtime_init.add_argument("--project", required=True)
    runtime_init.add_argument("--title", required=True)
    runtime_init.add_argument("--output")
    runtime_init.add_argument("--owner", default="unassigned")
    runtime_init.add_argument("--intent-json", default="")

    runtime_checkpoint = runtime_commands.add_parser("checkpoint", help="write an explicit Unit checkpoint")
    runtime_checkpoint.add_argument("--unit", required=True)
    runtime_checkpoint.add_argument("--completed", action="append", default=[])
    runtime_checkpoint.add_argument("--pending", action="append", default=[])
    runtime_checkpoint.add_argument("--blocked-by", dest="blocked_by", action="append", default=[])
    runtime_checkpoint.add_argument("--next-action", required=True)

    runtime_amend = runtime_commands.add_parser(
        "amend", help="record a user-requested change to the active Unit"
    )
    runtime_amend.add_argument("--unit", required=True)
    runtime_amend.add_argument("--request", required=True)
    runtime_amend.add_argument("--reason", default="")
    runtime_amend.add_argument(
        "--affected-artifact", dest="affected_artifacts", action="append", required=True
    )
    runtime_amend.add_argument("--requested-by", dest="requested_by", required=True)

    runtime_active_unit_detach = runtime_commands.add_parser(
        "active-unit-detach",
        help="release an unfinished active Unit after an explicit user decision",
    )
    runtime_active_unit_detach.add_argument("--project", default=".")
    runtime_active_unit_detach.add_argument("--unit", required=True)
    runtime_active_unit_detach.add_argument("--requested-by", dest="requested_by", required=True)
    runtime_active_unit_detach.add_argument("--reason", required=True)

    runtime_envelope_propose = runtime_commands.add_parser(
        "envelope-propose", help="propose an adaptive Execution Envelope for a Unit"
    )
    runtime_envelope_propose.add_argument("--unit", required=True)
    runtime_envelope_propose.add_argument("--scope", action="append", required=True)
    runtime_envelope_propose.add_argument("--stages-json", required=True)
    runtime_envelope_propose.add_argument("--allowed-action", action="append", required=True)
    runtime_envelope_propose.add_argument("--forbidden-action", action="append", default=[])
    runtime_envelope_propose.add_argument(
        "--external-access-json",
        default="[]",
        help="JSON list of L2 development/test external API policies",
    )
    runtime_envelope_propose.add_argument("--max-iterations", type=int, required=True)
    runtime_envelope_propose.add_argument("--proposed-by", required=True)
    runtime_envelope_propose.add_argument(
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

    runtime_envelope_approve = runtime_commands.add_parser(
        "envelope-approve",
        help="activate a proposed Execution Envelope from its approved inception Decision",
    )
    runtime_envelope_approve.add_argument("--unit", required=True)

    runtime_authorize = runtime_commands.add_parser(
        "authorize", help="check an action against the approved Execution Envelope"
    )
    runtime_authorize.add_argument("--unit", required=True)
    runtime_authorize.add_argument("--action", dest="requested_action", required=True)
    runtime_authorize.add_argument("--target", required=True)
    runtime_authorize.add_argument("--stage")
    runtime_authorize.add_argument("--method")
    runtime_authorize.add_argument("--credential-ref")

    runtime_managed_edit = runtime_commands.add_parser(
        "managed-edit",
        help="authorize and apply a Project file edit batch inside Core",
    )
    runtime_managed_edit.add_argument("--unit", required=True)
    runtime_managed_edit.add_argument("--changes-json", required=True)

    runtime_artifact_write = runtime_commands.add_parser(
        "artifact-write",
        help="persist human-facing Unit documents through Core",
    )
    runtime_artifact_write.add_argument("--unit", required=True)
    runtime_artifact_write.add_argument("--artifacts-json", required=True)

    runtime_managed_test = runtime_commands.add_parser(
        "managed-test",
        help="authorize and execute a test command inside Core",
    )
    runtime_managed_test.add_argument("--unit", required=True)
    runtime_managed_test.add_argument("--target", required=True)
    runtime_managed_test.add_argument("--command-json", required=True)
    runtime_managed_test.add_argument(
        "--timeout-seconds", type=int, default=300
    )

    runtime_evidence = runtime_commands.add_parser("evidence", help="record structured verification Evidence")
    runtime_evidence.add_argument("--unit", required=True)
    runtime_evidence.add_argument("--passed", action="store_true")
    runtime_evidence.add_argument("--commands-json", required=True)
    runtime_evidence.add_argument("--scope", required=True)
    runtime_evidence.add_argument("--recorded-by", dest="recorded_by", required=True)
    runtime_evidence.add_argument("--notes", default="")

    runtime_decision = runtime_commands.add_parser("decision", help="record a human Unit Decision")
    runtime_decision.add_argument("--unit", required=True)
    runtime_decision.add_argument(
        "--gate",
        choices=("inception", "architecture", "release", "operation", "knowledge"),
        required=True,
    )
    runtime_decision.add_argument("--outcome", choices=("approved", "rejected"), required=True)
    runtime_decision.add_argument("--summary", required=True)
    runtime_decision.add_argument("--rationale", action="append", required=True)
    runtime_decision.add_argument("--alternatives-json", default="[]")
    runtime_decision.add_argument("--tradeoff", action="append", default=[])
    runtime_decision.add_argument("--risk", action="append", default=[])
    runtime_decision.add_argument("--reference", action="append", default=[])
    runtime_decision.add_argument("--decided-by", dest="decided_by", required=True)

    runtime_transition = runtime_commands.add_parser(
        "transition", help="transition a Unit through an allowed lifecycle edge"
    )
    runtime_transition.add_argument("--unit", required=True)
    runtime_transition.add_argument(
        "--to",
        choices=(
            "proposed",
            "inception",
            "awaiting-inception-decision",
            "construction",
            "validation",
            "awaiting-release-decision",
            "releasing",
            "operating",
            "learned",
        ),
        required=True,
    )

    runtime_verify = runtime_commands.add_parser("verify", help="verify a Unit through the runtime contract")
    runtime_verify.add_argument("--unit", required=True)

    return parser
