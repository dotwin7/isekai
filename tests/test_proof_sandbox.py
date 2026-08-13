from __future__ import annotations

import base64
import json
import os
import platform
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from isekai.catalog.ai_dlc.unit.managed_execution import execute_proof
from isekai.catalog.ai_dlc.unit.proof_runtime import copy_test_workspace
from isekai.catalog.ai_dlc.unit.proof_sandbox import (
    PROOF_ADDRESS_SPACE_BYTES,
    PROOF_FILE_SIZE_BYTES,
    PROOF_OPEN_FILES,
    PROOF_PROCESSES,
    _linux_invocation,
    _macos_invocation,
    _symlink_read_roots,
    require_sandbox_provider,
    sandbox_available,
    sandbox_status,
)
from isekai.workflow.errors import WorkflowError

from test_execution_envelope import approve_inception, make_enveloped_unit


def test_proof_workspace_ignores_only_the_project_unit_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "copy"
    (source / "units" / "managed-unit").mkdir(parents=True)
    (source / "units" / "managed-unit" / "unit.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (source / "tests" / "units").mkdir(parents=True)
    nested_test = source / "tests" / "units" / "test_domain_units.py"
    nested_test.write_text("def test_nested_units():\n    assert True\n", encoding="utf-8")

    copy_test_workspace(source, destination)

    assert not (destination / "units").exists()
    assert (destination / "tests" / "units" / "test_domain_units.py").read_bytes() == (
        nested_test.read_bytes()
    )


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
    assert invocation.sandbox_policy == "seatbelt-deny-default-explicit-allowlist"
    assert "(deny default)" in profile
    assert "(allow default)" not in profile
    assert "(allow network" not in profile
    assert profile.count("(allow mach-lookup") == 1
    assert "com.apple.system.notification_center" in profile
    assert "(allow mach-register" not in profile
    assert "(allow signal (target self))" in profile
    assert "(allow process-info* (target self))" in profile
    assert "(allow file-read-metadata (require-any" in profile
    assert "(allow file-read-metadata)" not in profile
    assert "(allow file-read-data (require-any" in profile
    assert "(allow file-map-executable (require-any" in profile
    assert "(allow file-write* (require-any" in profile
    assert '(subpath "/dev")' not in profile
    assert '(literal "/dev/null")' in profile
    assert '(subpath "/Library")' not in profile
    assert '(subpath "/private/etc")' not in profile
    assert '(subpath "/private/var/db")' not in profile
    timezone_rule = '(subpath "/private/var/db/timezone")'
    assert (timezone_rule in profile) == Path("/private/var/db/timezone").exists()
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


@pytest.mark.skipif(
    platform.system() != "Darwin" or not sandbox_available(),
    reason="requires the macOS Seatbelt provider",
)
def test_real_macos_sandbox_hides_home_project_metadata(tmp_path: Path) -> None:
    source_project = Path(__file__).resolve().parents[1]
    source_file = source_project / "README.md"
    workspace = tmp_path / "project"
    workspace.mkdir()
    program = (
        "from pathlib import Path; import sys; "
        "\ntry:\n Path(sys.argv[1]).stat(); blocked=False"
        "\nexcept OSError:\n blocked=True"
        "\nprint('blocked' if blocked else 'visible')"
        "\nraise SystemExit(0 if blocked else 3)"
    )
    invocation = _macos_invocation(
        [sys.executable, "-c", program, str(source_file)],
        temp_root=tmp_path,
        workspace=workspace,
        source_project=source_project,
        environment=os.environ,
    )

    completed = subprocess.run(
        invocation.argv,
        cwd=workspace,
        env=invocation.environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "blocked"


def test_macos_provider_preflight_uses_the_deny_default_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []
    monkeypatch.setattr(
        "isekai.catalog.ai_dlc.unit.proof_sandbox.platform.system",
        lambda: "Darwin",
    )
    monkeypatch.setattr(
        "isekai.catalog.ai_dlc.unit.proof_sandbox.shutil.which",
        lambda name, **_kwargs: (
            "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None
        ),
    )

    def probe(command: list[str]) -> tuple[bool, str | None]:
        observed.extend(command)
        return True, None

    monkeypatch.setattr(
        "isekai.catalog.ai_dlc.unit.proof_sandbox._probe",
        probe,
    )

    status = sandbox_status()

    assert status == {
        "ready": True,
        "provider": "macos-seatbelt",
        "issue": None,
    }
    profile = observed[observed.index("-p") + 1]
    assert "(deny default)" in profile
    assert "(allow default)" not in profile


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


def test_macos_provider_allows_intermediate_toolchain_symlink_paths(
    tmp_path: Path,
) -> None:
    cellar = tmp_path / "Cellar/python/3.14/bin"
    cellar.mkdir(parents=True)
    executable = cellar / "python3.14"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    opt = tmp_path / "opt"
    opt.mkdir()
    alias = opt / "python@3.14"
    alias.symlink_to(cellar.parent)

    roots = _symlink_read_roots(alias / "bin/python3.14")

    assert alias in roots


def test_providers_expose_only_declared_project_dependencies_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_project = tmp_path / "source-project"
    dependency = source_project / "services/frontend/node_modules"
    dependency.mkdir(parents=True)
    workspace = tmp_path / "sandbox/project"
    workspace.mkdir(parents=True)
    real_which = __import__("shutil").which

    def which(name: str, **kwargs: object) -> str | None:
        if name == "sandbox-exec":
            return "/usr/bin/sandbox-exec"
        if name == "bwrap":
            return "/usr/bin/bwrap"
        return real_which(name, **kwargs)

    monkeypatch.setattr(
        "isekai.catalog.ai_dlc.unit.proof_sandbox.shutil.which",
        which,
    )

    macos = _macos_invocation(
        [sys.executable, "-c", "print('ok')"],
        temp_root=tmp_path / "sandbox",
        workspace=workspace,
        source_project=source_project,
        dependency_roots=[dependency],
        environment={"PATH": "/usr/bin:/bin"},
    )
    linux = _linux_invocation(
        [sys.executable, "-c", "print('ok')"],
        temp_root=tmp_path / "sandbox",
        workspace=workspace,
        source_project=source_project,
        dependency_roots=[dependency],
        environment={"PATH": "/usr/bin:/bin"},
    )

    assert json.dumps(str(dependency)) in macos.argv[2]
    dependency_index = linux.argv.index(str(dependency))
    assert linux.argv[dependency_index - 1] == "--ro-bind"
    assert linux.argv[dependency_index + 1] == str(dependency)


@pytest.mark.skipif(
    platform.system() != "Darwin" or not sandbox_available(),
    reason="requires the macOS Seatbelt provider",
)
def test_real_macos_sandbox_executes_a_prepared_node_dependency(
    tmp_path: Path,
) -> None:
    source_project = tmp_path / "source-project"
    dependency = source_project / "services/frontend/node_modules"
    tool = dependency / ".bin/tsc"
    tool.parent.mkdir(parents=True)
    tool.write_text("#!/bin/sh\necho typecheck-ok\n", encoding="utf-8")
    tool.chmod(0o755)
    workspace = tmp_path / "sandbox/project"
    workspace.mkdir(parents=True)
    workspace_dependency = workspace / "services/frontend/node_modules"
    workspace_dependency.parent.mkdir(parents=True)
    workspace_dependency.symlink_to(dependency, target_is_directory=True)
    invocation = _macos_invocation(
        ["services/frontend/node_modules/.bin/tsc"],
        temp_root=tmp_path / "sandbox",
        workspace=workspace,
        source_project=source_project,
        dependency_roots=[dependency],
        environment=os.environ,
    )

    completed = subprocess.run(
        invocation.argv,
        cwd=workspace,
        env=invocation.environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "typecheck-ok\n"


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
    source_file.stat()
    source_metadata_blocked = False
except OSError:
    source_metadata_blocked = True
try:
    external_file.stat()
    external_metadata_blocked = False
except OSError:
    external_metadata_blocked = True
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
    'source_metadata_blocked': source_metadata_blocked,
    'external_metadata_blocked': external_metadata_blocked,
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
    source_metadata_blocked,
    external_metadata_blocked,
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
        "source_metadata_blocked": True,
        "external_metadata_blocked": True,
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
