from __future__ import annotations

import argparse

from ..distribution import MANIFEST_PATH
from ..workflow import (
    EXECUTION_ENVELOPE_DEFAULT_HOURS,
    EXECUTION_ENVELOPE_MAX_HOURS,
    WorkRoute,
)


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
