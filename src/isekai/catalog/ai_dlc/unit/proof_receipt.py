from __future__ import annotations

import re
from typing import Any

from .common import _parse_iso_timestamp
from .proof_runtime import proof_command_text, proof_output_digest


RECEIPT_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


def _legacy_proof_receipt_issues(
    execution: dict[str, Any],
    command: dict[str, Any],
    *,
    passed: bool,
) -> list[str]:
    issues: list[str] = []
    status = execution.get("status")
    if not isinstance(status, str) or status not in {
        "completed",
        "timed-out",
        "output-limit-exceeded",
    }:
        issues.append("has an invalid legacy Core proof execution status")
    if passed and status != "completed":
        issues.append("cannot pass with an incomplete legacy Core proof execution")
    if execution.get("workspace") != "disposable-copy":
        issues.append("has an invalid legacy Core proof workspace")
    if execution.get("network_isolation") != "denied":
        issues.append("has an invalid legacy Core proof network boundary")
    if not isinstance(execution.get("sandbox_provider"), str) or not execution[
        "sandbox_provider"
    ].strip():
        issues.append("has no legacy Core proof sandbox provider")
    if execution.get("exit_code") != command.get("exit_code"):
        issues.append("exit_code does not match its legacy Core proof receipt")
    return issues


def proof_receipt_issues(
    grant: dict[str, Any],
    command: dict[str, Any],
    *,
    passed: bool,
) -> list[str]:
    execution = grant.get("execution")
    if not isinstance(execution, dict):
        return ["is not bound to a Core proof execution receipt"]
    if execution.get("type") == "core-managed-test":
        # Read compatibility for immutable Evidence created before the action
        # was renamed from managed-test to prove.
        return _legacy_proof_receipt_issues(execution, command, passed=passed)
    if execution.get("type") != "core-proof":
        return ["is not bound to a Core proof execution receipt"]

    issues: list[str] = []
    status = execution.get("status")
    if not isinstance(status, str) or status not in {
        "completed",
        "timed-out",
        "output-limit-exceeded",
    }:
        issues.append("has an invalid Core proof execution status")
    timed_out = execution.get("timed_out")
    output_limit_exceeded = execution.get("output_limit_exceeded")
    if not isinstance(timed_out, bool):
        issues.append("has no boolean timed_out result")
    if not isinstance(output_limit_exceeded, bool):
        issues.append("has no boolean output_limit_exceeded result")
    if status == "completed" and (
        timed_out is not False or output_limit_exceeded is not False
    ):
        issues.append("has inconsistent completed execution flags")
    if status == "timed-out" and timed_out is not True:
        issues.append("has inconsistent timed-out execution flags")
    if status == "output-limit-exceeded" and output_limit_exceeded is not True:
        issues.append("has inconsistent output-limit execution flags")
    if passed and status != "completed":
        issues.append("cannot pass with an incomplete Core proof execution")
    if execution.get("workspace") != "disposable-copy":
        issues.append("has an invalid Core proof workspace")
    if execution.get("filesystem_isolation") != (
        "source-and-user-data-read-denied-write-confined"
    ):
        issues.append("has an invalid Core proof filesystem boundary")
    if execution.get("network_isolation") != "denied":
        issues.append("has an invalid Core proof network boundary")
    if not isinstance(execution.get("process_isolation"), str) or execution.get(
        "process_isolation"
    ) not in {
        "process-group-cleanup",
        "seatbelt-process-access-denied-and-process-group-cleanup",
        "pid-namespace-and-process-group-cleanup",
    }:
        issues.append("has an invalid Core proof process boundary")
    if not isinstance(execution.get("sandbox_provider"), str) or not execution[
        "sandbox_provider"
    ].strip():
        issues.append("has no Core proof sandbox provider")
    sandbox_policy = execution.get("sandbox_policy")
    if sandbox_policy is not None and sandbox_policy not in {
        "provider-deny-default-explicit-allowlist",
        "seatbelt-deny-default-explicit-allowlist",
    }:
        issues.append("has an invalid Core proof sandbox policy")
    if execution.get("environment") != "core-allowlisted":
        issues.append("has an invalid Core proof environment boundary")
    dependency_views = execution.get("dependency_views")
    if dependency_views is not None and (
        not isinstance(dependency_views, list)
        or any(
            not isinstance(value, str)
            or not value
            or value.startswith("/")
            or ".." in value.split("/")
            or value.split("/")[-1] not in {".venv", "node_modules"}
            for value in dependency_views
        )
        or len(dependency_views) != len(set(dependency_views))
    ):
        issues.append("has invalid Core proof dependency views")
    limits = execution.get("resource_limits")
    required_limits = {
        "cpu_seconds",
        "file_size_bytes",
        "open_files",
        "processes",
        "core_dump_bytes",
    }
    if not isinstance(limits, dict) or not required_limits <= limits.keys() or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in limits.values()
    ):
        issues.append("has invalid Core proof resource limits")
    receipt_command = execution.get("command")
    if not isinstance(receipt_command, list) or not receipt_command or any(
        not isinstance(item, str) or not item for item in receipt_command
    ):
        issues.append("has an invalid Core proof command")
    elif execution.get("evidence_command") != proof_command_text(receipt_command):
        issues.append("has an invalid Core proof Evidence command binding")
    elif command.get("command") != execution.get("evidence_command"):
        issues.append("command does not match its Core proof receipt")
    if execution.get("exit_code") != command.get("exit_code"):
        issues.append("exit_code does not match its Core proof receipt")
    for stream in ("stdout", "stderr"):
        digest = execution.get(f"{stream}_digest")
        byte_count = execution.get(f"{stream}_bytes")
        truncated = execution.get(f"{stream}_truncated")
        if (
            not isinstance(digest, str)
            or RECEIPT_DIGEST_PATTERN.fullmatch(digest) is None
        ):
            issues.append(f"has an invalid Core proof {stream} digest")
        if (
            not isinstance(byte_count, int)
            or isinstance(byte_count, bool)
            or byte_count < 0
        ):
            issues.append(f"has an invalid Core proof {stream} byte count")
        if not isinstance(truncated, bool):
            issues.append(f"has an invalid Core proof {stream} truncation flag")
    capture_limit = execution.get("output_capture_limit_bytes")
    if (
        not isinstance(capture_limit, int)
        or isinstance(capture_limit, bool)
        or capture_limit < 1
    ):
        issues.append("has an invalid Core proof output capture limit")
    expected_output_digest = proof_output_digest(execution)
    if execution.get("evidence_output_digest") != expected_output_digest:
        issues.append("has an invalid Core proof Evidence output binding")
    if command.get("output_digest") != expected_output_digest:
        issues.append("output_digest does not match its Core proof receipt")
    completed_at = _parse_iso_timestamp(execution.get("completed_at"))
    if completed_at is None:
        issues.append("has an invalid Core proof completion timestamp")
    if command.get("observed_at") != execution.get("completed_at"):
        issues.append("observed_at does not match its Core proof receipt")
    return issues
