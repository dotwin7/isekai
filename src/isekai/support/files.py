from __future__ import annotations

import os
import stat
from pathlib import Path


class UnsafeControlFile(ValueError):
    """Raised when a control file cannot be opened without following aliases."""


def metadata_is_path_alias(metadata: os.stat_result) -> bool:
    """Treat Windows reparse points (including junctions) like symlinks."""
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_point)


def _absolute(path: Path) -> Path:
    """Return an absolute lexical path without resolving symbolic links."""
    return Path(os.path.abspath(path))


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_from_descriptor(descriptor: int) -> tuple[bytes, os.stat_result]:
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise UnsafeControlFile("control file must be a single-link regular file")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            content = stream.read()
            refreshed = os.fstat(stream.fileno())
            if _metadata_identity(metadata) != _metadata_identity(refreshed):
                raise UnsafeControlFile("control file changed while it was being read")
            return content, refreshed
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_control_file_snapshot(
    path: str | Path,
    *,
    root: str | Path | None = None,
    label: str = "control file",
) -> tuple[bytes, os.stat_result]:
    """Read bytes and metadata without traversing symlink path segments.

    ``root`` is the trusted directory that contains ``path``. On platforms with
    ``openat`` support, every descendant is opened relative to an already-open
    directory descriptor so a concurrent path retarget cannot escape that root.
    """
    target = _absolute(Path(path).expanduser())
    trusted_root = (
        _absolute(Path(root).expanduser()) if root is not None else target.parent
    )
    try:
        relative = target.relative_to(trusted_root)
    except ValueError as exc:
        raise UnsafeControlFile(f"{label} escapes its trusted root: {target}") from exc
    if not relative.parts:
        raise UnsafeControlFile(f"{label} must name a file below its trusted root")

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    supports_openat = os.open in getattr(os, "supports_dir_fd", set())

    if supports_openat and nofollow:
        directory_descriptor: int | None = None
        try:
            directory_descriptor = os.open(
                trusted_root,
                os.O_RDONLY | directory | nofollow | cloexec,
            )
            for part in relative.parts[:-1]:
                next_descriptor = os.open(
                    part,
                    os.O_RDONLY | directory | nofollow | cloexec,
                    dir_fd=directory_descriptor,
                )
                os.close(directory_descriptor)
                directory_descriptor = next_descriptor
            descriptor = os.open(
                relative.parts[-1],
                os.O_RDONLY | nofollow | cloexec,
                dir_fd=directory_descriptor,
            )
            return _read_from_descriptor(descriptor)
        except FileNotFoundError:
            raise
        except UnsafeControlFile as exc:
            raise UnsafeControlFile(f"{label} {exc}: {target}") from exc
        except OSError as exc:
            raise UnsafeControlFile(
                f"{label} contains a symlink or cannot be opened safely: {target}"
            ) from exc
        finally:
            if directory_descriptor is not None:
                os.close(directory_descriptor)

    # Windows does not expose openat/O_NOFOLLOW. Check every lexical segment,
    # then bind the opened descriptor to the lstat result to fail closed if the
    # leaf changes between validation and open.
    root_metadata = os.lstat(trusted_root)
    if metadata_is_path_alias(root_metadata) or not stat.S_ISDIR(root_metadata.st_mode):
        raise UnsafeControlFile(f"{label} has an unsafe trusted root: {trusted_root}")
    current = trusted_root
    for index, part in enumerate(relative.parts):
        current /= part
        metadata = os.lstat(current)
        if metadata_is_path_alias(metadata):
            raise UnsafeControlFile(f"{label} contains a symlink: {current}")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise UnsafeControlFile(f"{label} has a non-directory parent: {current}")
    descriptor = os.open(target, os.O_RDONLY | cloexec)
    try:
        opened = os.fstat(descriptor)
    except Exception:
        os.close(descriptor)
        raise
    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
        os.close(descriptor)
        raise UnsafeControlFile(f"{label} changed while it was being opened: {target}")
    try:
        return _read_from_descriptor(descriptor)
    except UnsafeControlFile as exc:
        raise UnsafeControlFile(f"{label} {exc}: {target}") from exc


def read_control_file(
    path: str | Path,
    *,
    root: str | Path | None = None,
    label: str = "control file",
) -> bytes:
    """Read a stable, single-link regular file below a trusted root."""
    content, _ = read_control_file_snapshot(path, root=root, label=label)
    return content
