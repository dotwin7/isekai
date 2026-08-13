from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SandboxInvocation:
    argv: list[str]
    provider: str
    environment: dict[str, str]
    sandbox_policy: str = "provider-deny-default-explicit-allowlist"
    filesystem_isolation: str = "source-and-user-data-read-denied-write-confined"
    network_isolation: str = "denied"
    process_isolation: str = "process-group-cleanup"
    resource_limits: dict[str, int] = field(default_factory=dict)


PROOF_ADDRESS_SPACE_BYTES = 4 * 1024 * 1024 * 1024
PROOF_FILE_SIZE_BYTES = 256 * 1024 * 1024
PROOF_OPEN_FILES = 256
PROOF_PROCESSES = 512
_RESOURCE_SUPERVISOR = """\
import os
import resource
import sys

cpu, address_space, file_size, open_files, processes = map(int, sys.argv[1:6])
for name, value in (
    ("RLIMIT_CORE", 0),
    ("RLIMIT_CPU", cpu),
    ("RLIMIT_AS", address_space),
    ("RLIMIT_FSIZE", file_size),
    ("RLIMIT_NOFILE", open_files),
    ("RLIMIT_NPROC", processes),
):
    if value <= 0 and name != "RLIMIT_CORE":
        continue
    resource_id = getattr(resource, name, None)
    if resource_id is None:
        continue
    _soft, hard = resource.getrlimit(resource_id)
    limited = value if hard == resource.RLIM_INFINITY else min(value, hard)
    resource.setrlimit(resource_id, (limited, limited))
command = sys.argv[6:]
os.execv(command[0], command)
"""


def resource_limited_command(
    command: list[str],
    *,
    timeout_seconds: int,
    address_space_supported: bool,
) -> tuple[list[str], dict[str, int]]:
    limits = {
        "cpu_seconds": timeout_seconds + 5,
        "file_size_bytes": PROOF_FILE_SIZE_BYTES,
        "open_files": PROOF_OPEN_FILES,
        "processes": PROOF_PROCESSES,
        "core_dump_bytes": 0,
    }
    address_space = PROOF_ADDRESS_SPACE_BYTES if address_space_supported else 0
    if address_space_supported:
        limits["address_space_bytes"] = address_space
    wrapped = [
        str(Path(sys.executable).absolute()),
        "-I",
        "-c",
        _RESOURCE_SUPERVISOR,
        str(limits["cpu_seconds"]),
        str(address_space),
        str(limits["file_size_bytes"]),
        str(limits["open_files"]),
        str(limits["processes"]),
        *command,
    ]
    return wrapped, limits


__all__ = [
    "PROOF_ADDRESS_SPACE_BYTES",
    "PROOF_FILE_SIZE_BYTES",
    "PROOF_OPEN_FILES",
    "PROOF_PROCESSES",
    "SandboxInvocation",
    "resource_limited_command",
]
