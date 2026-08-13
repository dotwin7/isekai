from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from isekai.support.errors import WorkflowError
from .proof_runtime import validated_dependency_roots


from .proof_resources import (
    PROOF_ADDRESS_SPACE_BYTES as PROOF_ADDRESS_SPACE_BYTES,
    PROOF_FILE_SIZE_BYTES as PROOF_FILE_SIZE_BYTES,
    PROOF_OPEN_FILES as PROOF_OPEN_FILES,
    PROOF_PROCESSES as PROOF_PROCESSES,
    SandboxInvocation as SandboxInvocation,
    resource_limited_command,
)


_LINUX_SYSTEM_ROOTS = (
    "/bin",
    "/lib",
    "/lib64",
    "/sbin",
    "/usr",
)
_LINUX_SYSTEM_FILES = (
    "/etc/alternatives",
    "/etc/group",
    "/etc/hosts",
    "/etc/ld.so.cache",
    "/etc/nsswitch.conf",
    "/etc/passwd",
    "/etc/resolv.conf",
    "/etc/ssl",
)
_TRUSTED_EXECUTABLE_ROOTS = (
    "/bin",
    "/Library",
    "/opt/homebrew",
    "/sbin",
    "/System",
    "/usr",
    "/usr/local",
)
_MACOS_SYSTEM_READ_ROOTS = (
    "/bin",
    "/Library/Apple/System/Library",
    "/private/etc/group",
    "/private/etc/hosts",
    "/private/etc/passwd",
    "/private/etc/protocols",
    "/private/etc/services",
    "/private/etc/ssl",
    "/private/var/db/timezone",
    "/sbin",
    "/System",
    "/usr",
)
_MACOS_TOOLCHAIN_READ_ROOTS = (
    "/opt/homebrew",
    "/usr/local",
)
_MACOS_READABLE_DEVICES = (
    "/dev/null",
    "/dev/random",
    "/dev/urandom",
    "/dev/zero",
)
_MACOS_WRITABLE_DEVICES = (
    "/dev/null",
)


def _probe(command: list[str]) -> tuple[bool, str | None]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={"PATH": os.defpath},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if completed.returncode == 0:
        return True, None
    detail = (completed.stderr or completed.stdout).strip()
    return False, detail[:500] or f"provider exited {completed.returncode}"


def sandbox_status() -> dict[str, object]:
    system = platform.system()
    if system == "Darwin":
        executable = shutil.which("sandbox-exec")
        if executable is None:
            return {
                "ready": False,
                "provider": "macos-seatbelt",
                "issue": "sandbox-exec is not installed",
            }
        supervisor = Path(sys.executable).absolute()
        supervisor_resolved = supervisor.resolve()
        ready, issue = _probe(
            [
                executable,
                "-p",
                _macos_profile(
                    read_roots=[
                        *(Path(value) for value in _MACOS_SYSTEM_READ_ROOTS),
                        *(
                            Path(value)
                            for value in _MACOS_TOOLCHAIN_READ_ROOTS
                            if Path(value).exists()
                        ),
                        *_command_roots(
                            supervisor,
                            supervisor_resolved,
                            temp_root=Path("/private/tmp/isekai-proof-preflight"),
                        ),
                        *_symlink_read_roots(supervisor),
                        Path(executable).parent,
                    ],
                    write_root=None,
                    metadata_paths=_symlink_resolution_paths(supervisor),
                ),
                str(supervisor),
                "-I",
                "-c",
                "raise SystemExit(0)",
            ]
        )
        return {"ready": ready, "provider": "macos-seatbelt", "issue": issue}
    if system == "Linux":
        executable = shutil.which("bwrap")
        if executable is None:
            return {
                "ready": False,
                "provider": "linux-bubblewrap",
                "issue": "Bubblewrap (bwrap) is not installed",
            }
        command = [
            executable,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
        ]
        for value in _LINUX_SYSTEM_ROOTS:
            root = Path(value)
            if root.exists():
                command.extend(["--ro-bind", value, value])
        command.extend(
            ["--proc", "/proc", "--dev", "/dev", "--", "/usr/bin/true"]
        )
        ready, issue = _probe(command)
        return {"ready": ready, "provider": "linux-bubblewrap", "issue": issue}
    return {
        "ready": False,
        "provider": None,
        "issue": f"prove has no OS sandbox provider for {system}",
    }


def sandbox_available() -> bool:
    return sandbox_status()["ready"] is True


def require_sandbox_provider() -> str:
    status = sandbox_status()
    if status["ready"] is not True:
        provider = status.get("provider") or "unsupported-platform"
        issue = status.get("issue") or "provider preflight failed"
        raise WorkflowError(
            f"prove OS sandbox is unavailable ({provider}): {issue}"
        )
    return str(status["provider"])


def _resolve_executable(
    argv: list[str],
    *,
    workspace: Path,
    environment: Mapping[str, str],
) -> tuple[Path, Path, list[str]]:
    requested = Path(argv[0])
    if requested.is_absolute():
        lexical = requested
    elif len(requested.parts) > 1:
        lexical = workspace / requested
    else:
        found = shutil.which(argv[0], path=environment.get("PATH"))
        if found is None:
            raise WorkflowError(
                f"proof executable is not on the allowlisted PATH: {argv[0]}"
            )
        lexical = Path(found)
    lexical = lexical.absolute()
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise WorkflowError(
            f"proof executable cannot be resolved: {lexical}: {exc}"
        ) from exc
    if not resolved.is_file() or (os.name != "nt" and not os.access(resolved, os.X_OK)):
        raise WorkflowError(f"proof executable is not executable: {lexical}")
    return lexical, resolved, [str(lexical), *argv[1:]]


def _runtime_root(executable: Path) -> Path:
    parent = executable.parent
    if parent.name in {"bin", "Scripts"}:
        candidate = parent.parent
        if (candidate / "pyvenv.cfg").is_file() or (candidate / "lib").is_dir():
            return candidate
    return parent


def _minimal_roots(candidates: list[Path]) -> list[Path]:
    roots: list[Path] = []
    for candidate in sorted(
        {item.absolute() for item in candidates if item.exists()},
        key=lambda item: (len(item.parts), str(item)),
    ):
        if any(candidate == root or root in candidate.parents for root in roots):
            continue
        roots.append(candidate)
    return roots


def _command_roots(
    lexical: Path,
    resolved: Path,
    *,
    temp_root: Path,
) -> list[Path]:
    candidates = [_runtime_root(lexical), _runtime_root(resolved)]
    return [
        root
        for root in _minimal_roots(candidates)
        if root != temp_root and temp_root not in root.parents
    ]


def _execution_roots(
    lexical: Path,
    resolved: Path,
    *,
    temp_root: Path,
) -> list[Path]:
    supervisor = Path(sys.executable).absolute()
    supervisor_resolved = supervisor.resolve()
    return _minimal_roots(
        [
            *_command_roots(lexical, resolved, temp_root=temp_root),
            *_command_roots(
                supervisor,
                supervisor_resolved,
                temp_root=temp_root,
            ),
        ]
    )




def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _validate_executable_scope(
    lexical: Path,
    resolved: Path,
    *,
    workspace: Path,
    source_project: Path,
    temp_root: Path,
    dependency_roots: list[Path] | None = None,
) -> None:
    current = Path(sys.executable).absolute()
    current_resolved = current.resolve()
    current_roots = _minimal_roots(
        [_runtime_root(current), _runtime_root(current_resolved)]
    )
    system_roots = [
        Path(value) for value in _TRUSTED_EXECUTABLE_ROOTS if Path(value).exists()
    ]
    source_virtualenv = source_project / ".venv"

    lexical_allowed = (
        _inside(lexical, workspace)
        or _inside(lexical, temp_root)
        or _inside(lexical, source_virtualenv)
        or any(
            _inside(lexical, root)
            for root in [*system_roots, *current_roots, *(dependency_roots or [])]
        )
    )
    resolved_allowed = (
        _inside(resolved, workspace)
        or _inside(resolved, temp_root)
        or _inside(resolved, source_virtualenv)
        or any(
            _inside(resolved, root)
            for root in [*system_roots, *current_roots, *(dependency_roots or [])]
        )
    )
    if not lexical_allowed or not resolved_allowed:
        raise WorkflowError(
            "proof executable is outside trusted system, Core runtime, "
            f"Project .venv, or disposable workspace roots: {lexical}"
        )


def _sandbox_environment(
    environment: Mapping[str, str],
    *,
    lexical: Path,
    resolved: Path,
    temp_root: Path,
    dependency_roots: list[Path] | None = None,
) -> dict[str, str]:
    safe_roots = _minimal_roots(
        [
            *(Path(value) for value in _LINUX_SYSTEM_ROOTS),
            Path("/opt/homebrew"),
            Path("/usr/local"),
            *_execution_roots(lexical, resolved, temp_root=temp_root),
            *(dependency_roots or []),
            temp_root,
        ]
    )
    safe_path: list[str] = []
    for value in environment.get("PATH", os.defpath).split(os.pathsep):
        if not value:
            continue
        candidate = Path(value).absolute()
        if not any(
            candidate == root or root in candidate.parents for root in safe_roots
        ):
            continue
        text = str(candidate)
        if text not in safe_path:
            safe_path.append(text)
    executable_directory = str(lexical.parent)
    if executable_directory not in safe_path:
        safe_path.insert(0, executable_directory)
    sanitized = dict(environment)
    sanitized["PATH"] = os.pathsep.join(safe_path)
    return sanitized


def _seatbelt_string(value: Path) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def _symlink_resolution_paths(path: Path) -> list[Path]:
    """Return each symlink encountered while resolving an executable path."""
    pending = [path.absolute()]
    visited: set[Path] = set()
    links: set[Path] = set()
    while pending:
        candidate = pending.pop()
        if candidate in visited:
            continue
        visited.add(candidate)
        current = Path(candidate.anchor)
        parts = candidate.parts[1:] if candidate.anchor else candidate.parts
        for index, part in enumerate(parts):
            current /= part
            try:
                is_link = current.is_symlink()
            except OSError:
                break
            if not is_link:
                continue
            links.add(current)
            try:
                target = Path(os.readlink(current))
            except OSError:
                break
            expanded = target if target.is_absolute() else current.parent / target
            remaining = parts[index + 1 :]
            pending.append(expanded.joinpath(*remaining).absolute())
            break
    return sorted(links, key=lambda item: (len(item.parts), str(item)))


def _symlink_read_roots(path: Path) -> list[Path]:
    """Return readable aliases needed to exec a symlinked toolchain binary."""
    roots: list[Path] = []
    for link in _symlink_resolution_paths(path):
        roots.append(link if link.is_dir() else link.parent)
    return _minimal_roots(roots)


def _macos_metadata_filter(
    read_roots: list[Path],
    readable_devices: list[Path],
    metadata_paths: list[Path],
) -> str:
    roots = _minimal_roots(read_roots)
    ancestors: set[Path] = {Path("/")}
    for path in [*roots, *readable_devices, *metadata_paths]:
        absolute = path.absolute()
        ancestors.add(absolute)
        ancestors.update(absolute.parents)
    return " ".join(
        [
            *(f"(subpath {_seatbelt_string(item)})" for item in roots),
            *(
                f"(literal {_seatbelt_string(item)})"
                for item in sorted(
                    ancestors,
                    key=lambda value: (len(value.parts), str(value)),
                )
            ),
        ]
    )


def _macos_profile(
    *,
    read_roots: list[Path],
    write_root: Path | None,
    metadata_paths: list[Path] | None = None,
) -> str:
    readable_devices = [
        Path(value) for value in _MACOS_READABLE_DEVICES if Path(value).exists()
    ]
    minimal_read_roots = _minimal_roots(read_roots)
    read_filter = " ".join(
        [
            '(literal "/")',
            *(
                f"(subpath {_seatbelt_string(item)})"
                for item in minimal_read_roots
            ),
            *(
                f"(literal {_seatbelt_string(item)})"
                for item in readable_devices
            ),
        ]
    )
    write_filters = [
        *(
            f"(literal {_seatbelt_string(Path(value))})"
            for value in _MACOS_WRITABLE_DEVICES
            if Path(value).exists()
        ),
    ]
    if write_root is not None:
        write_filters.insert(0, f"(subpath {_seatbelt_string(write_root)})")
    profile = [
        "(version 1)",
        "(deny default)",
        # Proof commands may spawn child processes, but Seatbelt still denies
        # inspecting or signalling processes outside the sandboxed process.
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow signal (target self))",
        "(allow process-info* (target self))",
        # Language runtimes query kernel limits and platform information during
        # startup.  The class is explicit and read-only; writes remain denied.
        "(allow sysctl-read)",
        # Seatbelt path resolution and language-runtime startup require metadata
        # access to allowlisted roots and their literal ancestors. Sibling paths
        # remain undiscoverable and file contents use the narrower data filter.
        "(allow file-read-metadata (require-any "
        + _macos_metadata_filter(
            minimal_read_roots,
            readable_devices,
            metadata_paths or [],
        )
        + "))",
        # CPython registers for timezone updates during initialization on macOS.
        # This is the only bootstrap service lookup permitted by the profile.
        '(allow mach-lookup '
        '(global-name "com.apple.system.notification_center"))',
        f"(allow file-read-data (require-any {read_filter}))",
        f"(allow file-map-executable (require-any {read_filter}))",
    ]
    if write_filters:
        profile.append(
            "(allow file-write* (require-any " + " ".join(write_filters) + "))"
        )
    return " ".join(profile)


def _macos_invocation(
    argv: list[str],
    *,
    temp_root: Path,
    workspace: Path,
    source_project: Path,
    dependency_roots: list[Path] | None = None,
    environment: Mapping[str, str],
    timeout_seconds: int = 300,
) -> SandboxInvocation:
    executable = shutil.which("sandbox-exec")
    if executable is None:  # pragma: no cover - guarded by provider preflight
        raise WorkflowError("prove OS sandbox is unavailable: sandbox-exec")
    lexical, resolved, command = _resolve_executable(
        argv,
        workspace=workspace,
        environment=environment,
    )
    dependencies = validated_dependency_roots(source_project, dependency_roots)
    _validate_executable_scope(
        lexical,
        resolved,
        workspace=workspace,
        source_project=source_project,
        temp_root=temp_root,
        dependency_roots=dependencies,
    )
    sandbox_environment = _sandbox_environment(
        environment,
        lexical=lexical,
        resolved=resolved,
        temp_root=temp_root,
        dependency_roots=dependencies,
    )
    read_roots = _minimal_roots(
        [
            *(Path(value) for value in _MACOS_SYSTEM_READ_ROOTS),
            *(
                Path(value)
                for value in _MACOS_TOOLCHAIN_READ_ROOTS
                if Path(value).exists()
            ),
            *_execution_roots(lexical, resolved, temp_root=temp_root),
            *_symlink_read_roots(lexical),
            *_symlink_read_roots(Path(sys.executable).absolute()),
            *dependencies,
            temp_root,
        ]
    )
    supervisor = Path(sys.executable).absolute()
    profile = _macos_profile(
        read_roots=read_roots,
        write_root=temp_root,
        metadata_paths=[
            *_symlink_resolution_paths(lexical),
            *_symlink_resolution_paths(supervisor),
        ],
    )
    limited_command, limits = resource_limited_command(
        command,
        timeout_seconds=timeout_seconds,
        address_space_supported=False,
    )
    return SandboxInvocation(
        argv=[executable, "-p", profile, *limited_command],
        provider="macos-seatbelt",
        environment=sandbox_environment,
        sandbox_policy="seatbelt-deny-default-explicit-allowlist",
        process_isolation="seatbelt-process-access-denied-and-process-group-cleanup",
        resource_limits=limits,
    )


def _linux_invocation(
    argv: list[str],
    *,
    temp_root: Path,
    workspace: Path,
    source_project: Path,
    dependency_roots: list[Path] | None = None,
    environment: Mapping[str, str],
    timeout_seconds: int = 300,
) -> SandboxInvocation:
    executable = shutil.which("bwrap")
    if executable is None:  # pragma: no cover - guarded by provider preflight
        raise WorkflowError("prove OS sandbox is unavailable: bwrap")
    lexical, resolved, command = _resolve_executable(
        argv,
        workspace=workspace,
        environment=environment,
    )
    dependencies = validated_dependency_roots(source_project, dependency_roots)
    _validate_executable_scope(
        lexical,
        resolved,
        workspace=workspace,
        source_project=source_project,
        temp_root=temp_root,
        dependency_roots=dependencies,
    )
    sandbox_environment = _sandbox_environment(
        environment,
        lexical=lexical,
        resolved=resolved,
        temp_root=temp_root,
        dependency_roots=dependencies,
    )
    read_roots = _minimal_roots(
        [
            *(Path(value) for value in _LINUX_SYSTEM_ROOTS),
            *(Path(value) for value in _LINUX_SYSTEM_FILES),
            *_execution_roots(lexical, resolved, temp_root=temp_root),
            *dependencies,
        ]
    )
    wrapped = [
        executable,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--clearenv",
    ]
    for key, value in sorted(sandbox_environment.items()):
        wrapped.extend(["--setenv", key, value])
    for root in read_roots:
        wrapped.extend(["--ro-bind", str(root), str(root)])
    limited_command, limits = resource_limited_command(
        command,
        timeout_seconds=timeout_seconds,
        address_space_supported=True,
    )
    wrapped.extend(
        [
            "--bind",
            str(temp_root),
            str(temp_root),
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--chdir",
            str(workspace),
            "--",
            *limited_command,
        ]
    )
    return SandboxInvocation(
        argv=wrapped,
        provider="linux-bubblewrap",
        environment=sandbox_environment,
        process_isolation="pid-namespace-and-process-group-cleanup",
        resource_limits=limits,
    )


def build_sandbox_invocation(
    argv: list[str],
    *,
    temp_root: Path,
    workspace: Path,
    source_project: Path,
    dependency_roots: list[Path] | None = None,
    environment: Mapping[str, str],
    timeout_seconds: int = 300,
) -> SandboxInvocation:
    provider = require_sandbox_provider()
    if provider == "macos-seatbelt":
        return _macos_invocation(
            argv,
            temp_root=temp_root,
            workspace=workspace,
            source_project=source_project,
            dependency_roots=dependency_roots,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
    if provider == "linux-bubblewrap":
        return _linux_invocation(
            argv,
            temp_root=temp_root,
            workspace=workspace,
            source_project=source_project,
            dependency_roots=dependency_roots,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
    raise WorkflowError(f"unsupported prove sandbox provider: {provider}")
