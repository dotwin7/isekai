from __future__ import annotations

import json
import os
import secrets
import stat
import tempfile
from pathlib import Path
from typing import Any

from .files import metadata_is_path_alias


class UnsafeWritePath(ValueError):
    """Raised when a rooted atomic write cannot keep its path below the root."""


def write_json_atomic(path: str | Path, value: Any) -> Path:
    """Write ``value`` as JSON so readers never observe a partial document.

    The payload is written to a same-directory temporary file, flushed to disk,
    and moved into place with ``os.replace``. Every ISEKAI artifact that another
    session may read - locks, ledgers, Decisions, Evidence, checkpoints - goes
    through this function so an interrupted process cannot truncate a record.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    _fsync_directory(target.parent)
    return target


def write_bytes_atomic(
    path: str | Path,
    content: bytes,
    *,
    mode: int | None = None,
) -> Path:
    """Atomically restore exact file bytes, optionally preserving its mode."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    _fsync_directory(target.parent)
    return target


def _rooted_relative_path(root: Path, relative: str | Path) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise UnsafeWritePath(
            f"atomic write path must stay below its trusted root: {relative}"
        )
    if any(part in {"", "."} for part in candidate.parts):
        raise UnsafeWritePath(f"atomic write path is not canonical: {relative}")
    return candidate


def _supports_rooted_writes() -> bool:
    return bool(
        getattr(os, "O_NOFOLLOW", 0)
        and os.open in os.supports_dir_fd
        and os.mkdir in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
        and os.rmdir in os.supports_dir_fd
        and os.rename in os.supports_dir_fd
        and os.link in os.supports_dir_fd
    )


def _open_rooted_parent(
    root: Path,
    relative: Path,
    *,
    create_parents: bool,
    parent_mode: int = 0o755,
) -> list[int]:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    try:
        root_descriptor = os.open(
            root,
            os.O_RDONLY | directory | nofollow | cloexec,
        )
    except OSError as exc:
        raise UnsafeWritePath(f"atomic write has an unsafe trusted root: {root}") from exc
    descriptors = [root_descriptor]
    try:
        for part in relative.parts[:-1]:
            try:
                descriptor = os.open(
                    part,
                    os.O_RDONLY | directory | nofollow | cloexec,
                    dir_fd=descriptors[-1],
                )
            except FileNotFoundError:
                if not create_parents:
                    raise
                try:
                    os.mkdir(part, mode=parent_mode, dir_fd=descriptors[-1])
                except FileExistsError:
                    pass
                try:
                    descriptor = os.open(
                        part,
                        os.O_RDONLY | directory | nofollow | cloexec,
                        dir_fd=descriptors[-1],
                    )
                except OSError as exc:
                    raise UnsafeWritePath(
                        f"atomic write parent is unsafe: {relative}"
                    ) from exc
            except OSError as exc:
                raise UnsafeWritePath(
                    f"atomic write parent is unsafe: {relative}"
                ) from exc
            metadata = os.fstat(descriptor)
            if metadata_is_path_alias(metadata) or not stat.S_ISDIR(metadata.st_mode):
                os.close(descriptor)
                raise UnsafeWritePath(f"atomic write parent is unsafe: {relative}")
            descriptors.append(descriptor)
        return descriptors
    except Exception:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def _rooted_leaf_metadata(parent_descriptor: int, name: str) -> os.stat_result | None:
    try:
        metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (
        metadata_is_path_alias(metadata)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise UnsafeWritePath(
            "atomic write target must be a single-link regular file or absent"
        )
    return metadata


def _write_bytes_atomic_descriptor(
    parent_descriptor: int,
    name: str,
    content: bytes,
    *,
    mode: int,
    replace_existing: bool,
) -> None:
    temporary_name: str | None = None
    descriptor = -1
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        for _attempt in range(100):
            # Keep the temporary leaf independent of the destination length.
            # A destination may be close to NAME_MAX and prefixing its full name
            # would otherwise make an otherwise valid atomic write fail.
            candidate = f".isekai-{secrets.token_hex(12)}.tmp"
            try:
                descriptor = os.open(
                    candidate,
                    flags,
                    0o600,
                    dir_fd=parent_descriptor,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_name is None or descriptor < 0:
            raise OSError("cannot allocate an atomic write temporary file")
        pending = memoryview(content)
        while pending:
            written = os.write(descriptor, pending)
            if written <= 0:
                raise OSError("atomic write made no forward progress")
            pending = pending[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if replace_existing:
            os.rename(
                temporary_name,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        else:
            os.link(
                temporary_name,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        temporary_name = None
        os.fsync(parent_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass


def _rooted_parent_matches(
    root_descriptor: int,
    parent_parts: tuple[str, ...],
    expected: os.stat_result,
) -> bool:
    descriptor = os.dup(root_descriptor)
    try:
        for part in parent_parts:
            next_descriptor = os.open(
                part,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        current = os.fstat(descriptor)
        return (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino)
    except OSError:
        return False
    finally:
        os.close(descriptor)


def _fallback_rooted_target(
    root: Path,
    relative: Path,
    *,
    create_parents: bool,
    parent_mode: int = 0o755,
) -> Path:
    root = Path(os.path.abspath(root))
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise UnsafeWritePath(f"atomic write has an unsafe trusted root: {root}") from exc
    if metadata_is_path_alias(root_metadata) or not stat.S_ISDIR(root_metadata.st_mode):
        raise UnsafeWritePath(f"atomic write has an unsafe trusted root: {root}")
    current = root
    for part in relative.parts[:-1]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if not create_parents:
                raise
            current.mkdir(mode=parent_mode)
            metadata = current.lstat()
        if metadata_is_path_alias(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise UnsafeWritePath(f"atomic write parent is unsafe: {relative}")
    target = current / relative.name
    try:
        target.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise UnsafeWritePath(f"atomic write path escapes its trusted root: {relative}") from exc
    return target


def write_bytes_atomic_beneath(
    root: str | Path,
    relative: str | Path,
    content: bytes,
    *,
    mode: int | None = None,
    create_parents: bool = False,
    replace_existing: bool = True,
    parent_mode: int = 0o755,
) -> Path:
    """Atomically write bytes without following a descendant path alias.

    On openat-capable platforms, the destination parent remains bound to an
    opened directory descriptor through the final rename. A concurrent rename
    or symlink substitution therefore cannot redirect the write to its target.
    """

    trusted_root = Path(os.path.abspath(Path(root).expanduser()))
    relative_path = _rooted_relative_path(trusted_root, relative)
    target = trusted_root / relative_path
    if not isinstance(content, bytes):
        raise TypeError("rooted atomic write content must be bytes")
    if not _supports_rooted_writes():
        fallback = _fallback_rooted_target(
            trusted_root,
            relative_path,
            create_parents=create_parents,
            parent_mode=parent_mode,
        )
        existing_mode = None
        try:
            metadata = fallback.lstat()
        except FileNotFoundError:
            pass
        else:
            if (
                metadata_is_path_alias(metadata)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
            ):
                raise UnsafeWritePath(
                    "atomic write target must be a single-link regular file or absent"
                )
            existing_mode = stat.S_IMODE(metadata.st_mode)
        if replace_existing:
            return write_bytes_atomic(
                fallback,
                content,
                mode=mode if mode is not None else existing_mode,
            )
        if existing_mode is not None:
            raise FileExistsError(fallback)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".isekai-rooted-", suffix=".tmp", dir=fallback.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode if mode is not None else 0o600)
            os.link(temporary, fallback, follow_symlinks=False)
        finally:
            temporary.unlink(missing_ok=True)
        _fsync_directory(fallback.parent)
        return fallback

    descriptors = _open_rooted_parent(
        trusted_root,
        relative_path,
        create_parents=create_parents,
        parent_mode=parent_mode,
    )
    try:
        parent_descriptor = descriptors[-1]
        parent_metadata = os.fstat(parent_descriptor)
        existing = _rooted_leaf_metadata(parent_descriptor, relative_path.name)
        if existing is not None and not replace_existing:
            raise FileExistsError(target)
        selected_mode = (
            mode
            if mode is not None
            else stat.S_IMODE(existing.st_mode) if existing is not None else 0o600
        )
        _write_bytes_atomic_descriptor(
            parent_descriptor,
            relative_path.name,
            content,
            mode=selected_mode,
            replace_existing=replace_existing,
        )
        if not _rooted_parent_matches(
            descriptors[0],
            relative_path.parts[:-1],
            parent_metadata,
        ):
            raise UnsafeWritePath(
                f"atomic write parent changed during the write: {relative_path}"
            )
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return target


def write_json_atomic_beneath(
    root: str | Path,
    relative: str | Path,
    value: Any,
    *,
    mode: int | None = None,
    create_parents: bool = False,
    replace_existing: bool = True,
    parent_mode: int = 0o755,
) -> Path:
    """Serialize JSON and persist it through :func:`write_bytes_atomic_beneath`."""

    content = (
        json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    return write_bytes_atomic_beneath(
        root,
        relative,
        content,
        mode=mode,
        create_parents=create_parents,
        replace_existing=replace_existing,
        parent_mode=parent_mode,
    )


def ensure_directory_beneath(
    root: str | Path,
    relative: str | Path,
    *,
    mode: int = 0o755,
) -> Path:
    """Create and validate a real descendant directory without following aliases."""

    trusted_root = Path(os.path.abspath(Path(root).expanduser()))
    relative_path = _rooted_relative_path(trusted_root, relative)
    target = trusted_root / relative_path
    probe = relative_path / ".isekai-directory-probe"
    if not _supports_rooted_writes():
        _fallback_rooted_target(
            trusted_root,
            probe,
            create_parents=True,
            parent_mode=mode,
        )
        return target
    descriptors = _open_rooted_parent(
        trusted_root,
        probe,
        create_parents=True,
        parent_mode=mode,
    )
    try:
        target_metadata = os.fstat(descriptors[-1])
        if not _rooted_parent_matches(
            descriptors[0],
            relative_path.parts,
            target_metadata,
        ):
            raise UnsafeWritePath(
                f"managed directory changed while it was created: {relative_path}"
            )
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return target


def unlink_file_beneath(
    root: str | Path,
    relative: str | Path,
    *,
    missing_ok: bool = False,
) -> None:
    """Unlink one regular file without following descendant path aliases."""

    trusted_root = Path(os.path.abspath(Path(root).expanduser()))
    relative_path = _rooted_relative_path(trusted_root, relative)
    if not _supports_rooted_writes():
        target = _fallback_rooted_target(
            trusted_root,
            relative_path,
            create_parents=False,
        )
        try:
            metadata = target.lstat()
        except FileNotFoundError:
            if missing_ok:
                return
            raise
        if (
            metadata_is_path_alias(metadata)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise UnsafeWritePath("atomic unlink target must be a single-link regular file")
        target.unlink()
        return

    try:
        descriptors = _open_rooted_parent(
            trusted_root,
            relative_path,
            create_parents=False,
        )
    except FileNotFoundError:
        if missing_ok:
            return
        raise
    try:
        parent_descriptor = descriptors[-1]
        rooted_metadata = _rooted_leaf_metadata(parent_descriptor, relative_path.name)
        if rooted_metadata is None:
            if missing_ok:
                return
            raise FileNotFoundError(relative_path)
        os.unlink(relative_path.name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def remove_empty_directory_beneath(
    root: str | Path,
    relative: str | Path,
    *,
    missing_ok: bool = False,
) -> None:
    """Remove one empty real directory without following descendant aliases."""

    trusted_root = Path(os.path.abspath(Path(root).expanduser()))
    relative_path = _rooted_relative_path(trusted_root, relative)
    if not _supports_rooted_writes():
        target = trusted_root / relative_path
        try:
            metadata = target.lstat()
        except FileNotFoundError:
            if missing_ok:
                return
            raise
        if metadata_is_path_alias(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise UnsafeWritePath("directory removal target must be a real directory")
        target.rmdir()
        return
    try:
        descriptors = _open_rooted_parent(
            trusted_root,
            relative_path,
            create_parents=False,
        )
    except FileNotFoundError:
        if missing_ok:
            return
        raise
    try:
        parent_descriptor = descriptors[-1]
        try:
            metadata = os.stat(
                relative_path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            if missing_ok:
                return
            raise
        if metadata_is_path_alias(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise UnsafeWritePath("directory removal target must be a real directory")
        os.rmdir(relative_path.name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _fsync_directory(directory: Path) -> None:
    """Persist the rename itself, not just the bytes it points at."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover - platforms without directory descriptors
        return
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover - filesystems that reject directory fsync
        pass
    finally:
        os.close(descriptor)


# Typed rooted-write contract shared with the locking layer.
fallback_rooted_target = _fallback_rooted_target
open_rooted_parent = _open_rooted_parent
rooted_relative_path = _rooted_relative_path
supports_rooted_writes = _supports_rooted_writes
