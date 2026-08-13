from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import stat
import subprocess
import time
from pathlib import Path
from typing import Any
from typing import Mapping

from isekai.support.errors import WorkflowError
from isekai.support.files import metadata_is_path_alias


MAX_PROOF_OUTPUT_BYTES = 256 * 1024
MAX_PROOF_CAPTURE_BYTES = 8 * 1024 * 1024
PROOF_IGNORED_NAMES = {
    ".git",
    ".isekai",
    ".isekai-runtime",
    "__pycache__",
    "units",
}
PROOF_DEPENDENCY_NAMES = {".venv", "node_modules"}


def validated_dependency_roots(
    source_project: Path,
    dependency_roots: list[Path] | None,
) -> list[Path]:
    source = source_project.absolute()
    validated: list[Path] = []
    for value in dependency_roots or []:
        lexical = value.absolute()
        try:
            lexical.relative_to(source)
        except ValueError as exc:
            raise WorkflowError(
                f"proof dependency root escapes its source Project: {lexical}"
            ) from exc
        if lexical.name not in PROOF_DEPENDENCY_NAMES:
            raise WorkflowError(
                f"proof dependency root has an unsupported name: {lexical}"
            )
        try:
            resolved = lexical.resolve(strict=True)
        except OSError as exc:
            raise WorkflowError(
                f"proof dependency root cannot be resolved: {lexical}"
            ) from exc
        if resolved != lexical or not lexical.is_dir():
            raise WorkflowError(
                f"proof dependency root must be a real Project directory: {lexical}"
            )
        if lexical not in validated:
            validated.append(lexical)
    return validated


def proof_command_text(command: list[str]) -> str:
    """Return the portable command representation stored in Evidence."""
    return json.dumps(command, ensure_ascii=False, separators=(",", ":"))


def proof_output_digest(execution: Mapping[str, Any]) -> str:
    """Bind Evidence to both complete output streams without retaining output."""
    subject = {
        field: execution.get(field)
        for field in (
            "stdout_digest",
            "stderr_digest",
            "stdout_bytes",
            "stderr_bytes",
            "stdout_truncated",
            "stderr_truncated",
        )
    }
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CapturedOutput:
    def __init__(self) -> None:
        self._digest = hashlib.sha256()
        self._returned = bytearray()
        self.byte_count = 0

    def add(self, content: bytes) -> None:
        self._digest.update(content)
        self.byte_count += len(content)
        remaining = MAX_PROOF_OUTPUT_BYTES - len(self._returned)
        if remaining > 0:
            self._returned.extend(content[:remaining])

    @property
    def digest(self) -> str:
        return "sha256:" + self._digest.hexdigest()

    @property
    def text(self) -> str:
        return bytes(self._returned).decode("utf-8", errors="replace")

    @property
    def truncated(self) -> bool:
        return self.byte_count > len(self._returned)


def isolated_test_command(argv: list[str], project_root: Path) -> list[str]:
    project_text = str(project_root)
    for argument in argv:
        if project_text in argument:
            raise WorkflowError(
                "proof argv cannot reference the writable source Project; "
                "use paths relative to the isolated test workspace"
            )
    return argv


def _source_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _verified_source_fd(
    parent_fd: int,
    name: str,
    metadata: os.stat_result,
    *,
    directory: bool,
    relative: Path,
) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise WorkflowError(
            f"proof source Project entry changed while copying: {relative}"
        ) from exc
    current = os.fstat(descriptor)
    if _source_identity(current) != _source_identity(metadata):
        os.close(descriptor)
        raise WorkflowError(
            f"proof source Project entry changed while copying: {relative}"
        )
    return descriptor


def _copy_regular_source_file(
    source_fd: int,
    destination: Path,
    metadata: os.stat_result,
    *,
    relative: Path,
) -> None:
    destination_fd = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        stat.S_IMODE(metadata.st_mode) & 0o777,
    )
    try:
        while content := os.read(source_fd, 1024 * 1024):
            pending = memoryview(content)
            while pending:
                written = os.write(destination_fd, pending)
                pending = pending[written:]
        os.fchmod(destination_fd, stat.S_IMODE(metadata.st_mode) & 0o777)
        if _source_identity(os.fstat(source_fd)) != _source_identity(metadata):
            raise WorkflowError(
                f"proof source Project entry changed while copying: {relative}"
            )
    finally:
        os.close(destination_fd)


def _copy_source_directory(
    source_fd: int,
    destination: Path,
    *,
    source_root: Path,
    dependency_roots: list[Path],
    relative_root: Path,
) -> None:
    entries = sorted(os.scandir(source_fd), key=lambda item: item.name)
    for entry in entries:
        if entry.name in PROOF_IGNORED_NAMES:
            continue
        relative = relative_root / entry.name
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise WorkflowError(
                f"proof source Project entry changed while copying: {relative}"
            ) from exc
        if metadata_is_path_alias(metadata):
            raise WorkflowError(
                "proof source Project cannot contain symlinks or junctions: "
                f"{relative}"
            )
        destination_entry = destination / entry.name
        if entry.name in PROOF_DEPENDENCY_NAMES:
            if not stat.S_ISDIR(metadata.st_mode):
                raise WorkflowError(
                    "proof dependency root must be a real directory: "
                    f"{relative}"
                )
            dependency_root = source_root / relative
            destination_entry.symlink_to(dependency_root, target_is_directory=True)
            dependency_roots.append(dependency_root)
            continue
        if stat.S_ISDIR(metadata.st_mode):
            child_fd = _verified_source_fd(
                source_fd,
                entry.name,
                metadata,
                directory=True,
                relative=relative,
            )
            try:
                destination_entry.mkdir(mode=stat.S_IMODE(metadata.st_mode) & 0o777)
                _copy_source_directory(
                    child_fd,
                    destination_entry,
                    source_root=source_root,
                    dependency_roots=dependency_roots,
                    relative_root=relative,
                )
                destination_entry.chmod(stat.S_IMODE(metadata.st_mode) & 0o777)
                if _source_identity(os.fstat(child_fd)) != _source_identity(metadata):
                    raise WorkflowError(
                        "proof source Project entry changed while copying: "
                        f"{relative}"
                    )
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1:
                raise WorkflowError(
                    "proof source Project cannot contain hardlinked files: "
                    f"{relative}"
                )
            child_fd = _verified_source_fd(
                source_fd,
                entry.name,
                metadata,
                directory=False,
                relative=relative,
            )
            try:
                _copy_regular_source_file(
                    child_fd,
                    destination_entry,
                    metadata,
                    relative=relative,
                )
            finally:
                os.close(child_fd)
        else:
            raise WorkflowError(
                "proof source Project cannot contain special files: "
                f"{relative}"
            )
        try:
            current = os.stat(entry.name, dir_fd=source_fd, follow_symlinks=False)
        except OSError as exc:
            raise WorkflowError(
                f"proof source Project entry changed while copying: {relative}"
            ) from exc
        if _source_identity(current) != _source_identity(metadata):
            raise WorkflowError(
                f"proof source Project entry changed while copying: {relative}"
            )


def copy_test_workspace(project_root: Path, destination: Path) -> list[Path]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
    try:
        source_fd = os.open(project_root, flags)
    except OSError as exc:
        raise WorkflowError(
            "proof source Project cannot be opened without following links"
        ) from exc
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISDIR(before.st_mode):
            raise WorkflowError("proof source Project must be a directory")
        destination.mkdir()
        dependency_roots: list[Path] = []
        _copy_source_directory(
            source_fd,
            destination,
            source_root=project_root,
            dependency_roots=dependency_roots,
            relative_root=Path(),
        )
        if _source_identity(os.fstat(source_fd)) != _source_identity(before):
            raise WorkflowError(
                "proof source Project changed while its workspace was copied"
            )
    finally:
        os.close(source_fd)
    return dependency_roots


def proof_environment(
    temp_root: Path,
    *,
    dependency_roots: list[Path] | None = None,
) -> dict[str, str]:
    home = temp_root / "home"
    temporary = temp_root / "tmp"
    home.mkdir()
    temporary.mkdir()
    allowed = {
        "COMSPEC",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
    }
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in allowed
    }
    environment.update(
        {
            "HOME": str(home),
            "PYTHONDONTWRITEBYTECODE": "1",
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "TMPDIR": str(temporary),
        }
    )
    dependency_bins = [
        str(root / ("Scripts" if os.name == "nt" else "bin"))
        for root in dependency_roots or []
        if root.name == ".venv"
        and (root / ("Scripts" if os.name == "nt" else "bin")).is_dir()
    ]
    if dependency_bins:
        current_path = environment.get("PATH", os.defpath)
        environment["PATH"] = os.pathsep.join([*dependency_bins, current_path])
    if os.name == "nt":
        environment["USERPROFILE"] = str(home)
    return environment


def _terminate_proof(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:  # pragma: no cover - exercised by the Windows integration job
        if process.poll() is None:
            process.kill()
    if process.poll() is None:
        process.wait()


def capture_managed_process(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: int,
) -> tuple[int | None, bool, bool, CapturedOutput, CapturedOutput]:
    assert process.stdout is not None
    assert process.stderr is not None
    captures = {
        "stdout": CapturedOutput(),
        "stderr": CapturedOutput(),
    }
    selector = selectors.DefaultSelector()
    for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    deadline = time.monotonic() + timeout_seconds
    cleanup_deadline: float | None = None
    timed_out = False
    output_limit_exceeded = False
    exit_code: int | None = None
    total_bytes = 0
    try:
        while selector.get_map() or cleanup_deadline is None:
            now = time.monotonic()
            if cleanup_deadline is None:
                completed = process.poll()
                if total_bytes >= MAX_PROOF_CAPTURE_BYTES:
                    output_limit_exceeded = True
                    _terminate_proof(process)
                    cleanup_deadline = now + 0.5
                elif completed is not None:
                    exit_code = completed
                    # A successful test may leave same-session background children.
                    # Terminate the whole group before closing inherited pipes.
                    _terminate_proof(process)
                    cleanup_deadline = now + 0.5
                elif now >= deadline:
                    timed_out = True
                    _terminate_proof(process)
                    cleanup_deadline = now + 0.5

            if cleanup_deadline is not None and now >= cleanup_deadline:
                break
            wait_seconds = 0.05
            if cleanup_deadline is None:
                wait_seconds = min(wait_seconds, max(0.0, deadline - now))
            events = selector.select(wait_seconds)
            for key, _event in events:
                remaining = MAX_PROOF_CAPTURE_BYTES - total_bytes
                if remaining <= 0:
                    break
                readable: Any = key.fileobj
                try:
                    content = os.read(
                        readable.fileno(), min(64 * 1024, remaining)
                    )
                except BlockingIOError:
                    continue
                if not content:
                    selector.unregister(readable)
                    readable.close()
                    continue
                captures[str(key.data)].add(content)
                total_bytes += len(content)
    finally:
        for key in list(selector.get_map().values()):
            fileobj: Any = key.fileobj
            selector.unregister(fileobj)
            fileobj.close()
        selector.close()
        if process.poll() is None:
            _terminate_proof(process)
    if timed_out or output_limit_exceeded:
        exit_code = None
    return (
        exit_code,
        timed_out,
        output_limit_exceeded,
        captures["stdout"],
        captures["stderr"],
    )
