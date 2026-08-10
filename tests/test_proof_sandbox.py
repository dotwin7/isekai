from __future__ import annotations

import base64
import json
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from isekai.catalog.ai_dlc.unit.managed_execution import execute_proof
from isekai.catalog.ai_dlc.unit.proof_sandbox import (
    PROOF_ADDRESS_SPACE_BYTES,
    PROOF_FILE_SIZE_BYTES,
    PROOF_OPEN_FILES,
    PROOF_PROCESSES,
    _linux_invocation,
    _macos_invocation,
    require_sandbox_provider,
    sandbox_available,
    sandbox_status,
)
from isekai.workflow.errors import WorkflowError

from test_execution_envelope import approve_inception, make_enveloped_unit


def test_unsupported_platform_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "isekai.catalog.ai_dlc.unit.proof_sandbox.platform.system",
        lambda: "Windows",
    )

    status = sandbox_status()

    assert status["ready"] is False
    assert "no OS sandbox provider" in str(status["issue"])
    with pytest.raises(WorkflowError, match="OS sandbox is unavailable"):
        require_sandbox_provider()


def test_macos_provider_allows_only_declared_reads_and_temp_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.setattr(
        "isekai.catalog.ai_dlc.unit.proof_sandbox.shutil.which",
        lambda name, **_kwargs: (
            "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None
        ),
    )

    invocation = _macos_invocation(
        [sys.executable, "-c", "print('ok')"],
        temp_root=tmp_path,
        workspace=workspace,
        source_project=tmp_path.parent / "source-project",
        environment={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home")},
    )

    profile = invocation.argv[2]
    assert invocation.provider == "macos-seatbelt"
    assert "(allow default)" in profile
    assert "(deny network*)" in profile
    assert "(deny mach-lookup)" in profile
    assert "(deny mach-register)" in profile
    assert "(deny signal (require-not (target self)))" in profile
    assert "(deny process-info* (require-not (target self)))" in profile
    assert "(deny file-read-data (require-not" in profile
    assert "(deny file-write* (require-not" in profile
    assert json.dumps(str(tmp_path)) in profile
    assert json.dumps(str(tmp_path.parent / "source-project")) not in profile
    assert str(tmp_path.parent / "outside") not in profile
    assert invocation.process_isolation == (
        "seatbelt-process-access-denied-and-process-group-cleanup"
    )
    assert invocation.resource_limits == {
        "cpu_seconds": 305,
        "file_size_bytes": PROOF_FILE_SIZE_BYTES,
        "open_files": PROOF_OPEN_FILES,
        "processes": PROOF_PROCESSES,
        "core_dump_bytes": 0,
    }


def test_linux_provider_unshares_network_and_binds_only_declared_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    real_which = __import__("shutil").which

    def which(name: str, **kwargs: object) -> str | None:
        if name == "bwrap":
            return "/usr/bin/bwrap"
        return real_which(name, **kwargs)

    monkeypatch.setattr(
        "isekai.catalog.ai_dlc.unit.proof_sandbox.shutil.which",
        which,
    )

    invocation = _linux_invocation(
        [sys.executable, "-c", "print('ok')"],
        temp_root=tmp_path,
        workspace=workspace,
        source_project=tmp_path.parent / "source-project",
        environment={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home")},
    )

    assert invocation.provider == "linux-bubblewrap"
    assert "--unshare-all" in invocation.argv
    assert "--share-net" not in invocation.argv
    bind_index = invocation.argv.index("--bind")
    assert invocation.argv[bind_index + 1 : bind_index + 3] == [
        str(tmp_path),
        str(tmp_path),
    ]
    assert str(tmp_path.parent / "outside") not in invocation.argv
    assert invocation.process_isolation == "pid-namespace-and-process-group-cleanup"
    assert invocation.resource_limits["cpu_seconds"] == 305
    assert invocation.resource_limits["address_space_bytes"] == (
        PROOF_ADDRESS_SPACE_BYTES
    )


def test_provider_rejects_an_executable_from_an_arbitrary_host_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "sandbox/project"
    workspace.mkdir(parents=True)
    executable = tmp_path / "host-tools/arbitrary-runner"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(
        "isekai.catalog.ai_dlc.unit.proof_sandbox.shutil.which",
        lambda name, **_kwargs: (
            "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None
        ),
    )

    with pytest.raises(WorkflowError, match="outside trusted"):
        _macos_invocation(
            [str(executable)],
            temp_root=tmp_path / "sandbox",
            workspace=workspace,
            source_project=tmp_path / "source-project",
            environment={"PATH": "/usr/bin:/bin"},
        )


def test_macos_provider_allows_a_project_virtualenv_console_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "sandbox/project"
    workspace.mkdir(parents=True)
    source_project = tmp_path / "source-project"
    executable = source_project / ".venv/bin/pytest"
    executable.parent.mkdir(parents=True)
    (source_project / ".venv/pyvenv.cfg").write_text(
        "home = /usr/bin\n",
        encoding="utf-8",
    )
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(
        "isekai.catalog.ai_dlc.unit.proof_sandbox.shutil.which",
        lambda name, **_kwargs: (
            "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None
        ),
    )

    invocation = _macos_invocation(
        [str(executable)],
        temp_root=tmp_path / "sandbox",
        workspace=workspace,
        source_project=source_project,
        environment={"PATH": str(executable.parent)},
    )

    assert invocation.provider == "macos-seatbelt"
    assert json.dumps(str(source_project / ".venv")) in invocation.argv[2]


@pytest.mark.skipif(
    not sandbox_available(),
    reason="prove OS sandbox provider is unavailable",
)
def test_real_sandbox_blocks_external_read_write_and_network(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    source_secret = tmp_path / "project/private.txt"
    source_secret.write_text("must-not-be-readable", encoding="utf-8")
    external_secret = tmp_path / "external-secret.txt"
    external_secret.write_text("also-must-not-be-readable", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(3)
    connected = threading.Event()
    sibling = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def accept_connection() -> None:
        try:
            connection, _address = server.accept()
        except OSError:
            return
        connection.close()
        connected.set()

    thread = threading.Thread(target=accept_connection, daemon=True)
    thread.start()
    port = server.getsockname()[1]
    encoded_source = base64.b64encode(str(source_secret).encode()).decode()
    encoded_external = base64.b64encode(str(external_secret).encode()).decode()
    program = """
import base64
import json
import os
import socket
import sys
import resource
from pathlib import Path

outside, port, sibling_pid = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
source_file = Path(base64.b64decode(sys.argv[4]).decode())
external_file = Path(base64.b64decode(sys.argv[5]).decode())
try:
    source_file.read_text()
    read_blocked = False
except OSError:
    read_blocked = True
try:
    external_file.read_text()
    external_read_blocked = False
except OSError:
    external_read_blocked = True
try:
    outside.write_text('escaped')
    write_blocked = False
except OSError:
    write_blocked = True
try:
    symlink = Path('symlink-secret')
    symlink.symlink_to(source_file)
    symlink.read_text()
    symlink_blocked = False
except OSError:
    symlink_blocked = True
try:
    hardlink = Path('hardlink-secret')
    os.link(source_file, hardlink)
    hardlink.read_text()
    hardlink_blocked = False
except OSError:
    hardlink_blocked = True
try:
    connection = socket.create_connection(('127.0.0.1', port), timeout=1)
    connection.close()
    network_blocked = False
except OSError:
    network_blocked = True
try:
    os.kill(sibling_pid, 0)
    external_process_blocked = False
except OSError:
    external_process_blocked = True
resource_limits_enforced = all((
    resource.getrlimit(resource.RLIMIT_CORE)[1] == 0,
    resource.getrlimit(resource.RLIMIT_CPU)[1] <= 305,
    resource.getrlimit(resource.RLIMIT_FSIZE)[1] <= 268435456,
    resource.getrlimit(resource.RLIMIT_NOFILE)[1] <= 256,
    resource.getrlimit(resource.RLIMIT_NPROC)[1] <= 512,
))
print(json.dumps({
    'read_blocked': read_blocked,
    'external_read_blocked': external_read_blocked,
    'write_blocked': write_blocked,
    'symlink_blocked': symlink_blocked,
    'hardlink_blocked': hardlink_blocked,
    'network_blocked': network_blocked,
    'external_process_blocked': external_process_blocked,
    'resource_limits_enforced': resource_limits_enforced,
}))
raise SystemExit(0 if all((
    read_blocked,
    external_read_blocked,
    write_blocked,
    symlink_blocked,
    hardlink_blocked,
    network_blocked,
    external_process_blocked,
    resource_limits_enforced,
)) else 3)
"""
    try:
        result = execute_proof(
            unit,
            target="tests/sandbox.py",
            command=[
                sys.executable,
                "-c",
                program,
                    str(outside),
                    str(port),
                    str(sibling.pid),
                    encoded_source,
                encoded_external,
            ],
        )
    finally:
        server.close()
        thread.join(timeout=3)
        sibling.terminate()
        sibling.wait(timeout=3)

    assert result["passed"] is True, (
        result["execution"],
        result["stdout"],
        result["stderr"],
    )
    assert json.loads(result["stdout"]) == {
        "read_blocked": True,
        "external_read_blocked": True,
        "write_blocked": True,
        "symlink_blocked": True,
        "hardlink_blocked": True,
        "network_blocked": True,
        "external_process_blocked": True,
        "resource_limits_enforced": True,
    }
    assert result["execution"]["filesystem_isolation"] == (
        "source-and-user-data-read-denied-write-confined"
    )
    assert result["execution"]["network_isolation"] == "denied"
    assert not outside.exists()
    assert connected.is_set() is False
