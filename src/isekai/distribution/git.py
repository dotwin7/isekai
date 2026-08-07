from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse

from .install import install_from_checkout, load_install_lock
from .release import (
    DistributionError,
    _normalize_runtimes,
    _verify_or_raise,
)


def _validate_git_source(source: str) -> str:
    if not isinstance(source, str) or not source.strip() or source.startswith("-"):
        raise DistributionError("Git source must be a non-empty path or URL")
    # `git clone ext::sh -c ...` runs the payload through a transport helper.
    # Only real locations are accepted; helper syntax never is.
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*::", source):
        raise DistributionError(
            f"Git source must not use a transport helper: {source}"
        )
    if "://" in source:
        parsed = urlparse(source)
        credentialed_http = (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.username is not None
        )
        if parsed.password is not None or credentialed_http:
            raise DistributionError(
                "Git source must not contain embedded credentials; "
                "use a credential helper or SSH agent"
            )
        if parsed.query or parsed.fragment:
            raise DistributionError(
                "Git source must not contain a query or fragment; "
                "use a credential-free canonical remote URL"
            )
    return source


def _git(command: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *command],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DistributionError(
            f"git {' '.join(command)} failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    return completed.stdout.strip()


def _resolve_immutable_git_ref(checkout: Path, ref: str) -> str:
    if re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", ref):
        try:
            commit = _git(["rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=checkout)
        except DistributionError as exc:
            raise DistributionError(f"Git commit does not exist: {ref}") from exc
        if commit.lower() != ref.lower():
            raise DistributionError(f"Git ref is not the requested full commit: {ref}")
        return commit
    try:
        return _git(
            ["rev-parse", "--verify", f"refs/tags/{ref}^{{commit}}"],
            cwd=checkout,
        )
    except DistributionError as exc:
        raise DistributionError(
            "Git ref must be an immutable tag or full commit; "
            f"branches and abbreviated commits are not allowed: {ref}"
        ) from exc


def _local_git_source(value: str) -> Path | None:
    if value.startswith("file://"):
        parsed = urlparse(value)
        if parsed.netloc not in {"", "localhost"}:
            return None
        return Path(unquote(parsed.path)).expanduser().resolve()
    if "://" in value or re.match(r"^[^/\\]+@[^:]+:", value):
        return None
    return Path(value).expanduser().resolve()


def _git_sources_match(expected: str, actual: str) -> bool:
    if expected.rstrip("/") == actual.rstrip("/"):
        return True
    expected_path = _local_git_source(expected)
    actual_path = _local_git_source(actual)
    return (
        expected_path is not None
        and actual_path is not None
        and expected_path == actual_path
    )


def _verify_checkout_source(checkout: Path, source: str) -> None:
    """Bind a bootstrap checkout to the Git source recorded in the lock."""
    try:
        origin = _git(["remote", "get-url", "origin"], cwd=checkout)
    except DistributionError:
        source_path = _local_git_source(source)
        if source_path == checkout:
            return
        raise DistributionError(
            "bootstrap checkout has no origin matching the requested Git source"
        ) from None
    if not _git_sources_match(source, origin):
        raise DistributionError(
            "bootstrap checkout origin does not match the requested Git source"
        )


def _reject_moved_ref(project: str | Path, source: str, ref: str, commit: str) -> None:
    current = load_install_lock(project)
    if not current:
        return
    locked_source = current.get("source", {})
    if (
        locked_source.get("git") == source
        and locked_source.get("ref") == ref
        and locked_source.get("commit") not in {None, commit}
    ):
        raise DistributionError(
            "Git ref moved to a different commit; use a new immutable tag"
        )


def install_from_git(
    source: str,
    ref: str,
    project: str | Path,
    *,
    runtimes: Iterable[str] = ("all",),
    update: bool = False,
    include_foundation: bool = False,
    adopt_foundation: bool = False,
) -> dict[str, Any]:
    _validate_git_source(source)
    if not isinstance(ref, str) or not ref.strip() or ref.startswith("-"):
        raise DistributionError("Git ref must be a non-empty immutable tag or full commit")
    with tempfile.TemporaryDirectory(prefix="isekai-release-") as temporary:
        checkout = Path(temporary) / "checkout"
        _git(["clone", "--quiet", "--no-checkout", source, str(checkout)])
        commit = _resolve_immutable_git_ref(checkout, ref)
        _git(["checkout", "--quiet", "--detach", commit], cwd=checkout)
        _reject_moved_ref(project, source, ref, commit)
        return install_from_checkout(
            checkout,
            project,
            source=source,
            ref=ref,
            commit=commit,
            runtimes=runtimes,
            update=update,
            include_foundation=include_foundation,
            adopt_foundation=adopt_foundation,
        )


def install_from_bootstrap_checkout(
    checkout: str | Path,
    source: str,
    ref: str,
    project: str | Path,
    *,
    runtimes: Iterable[str] = ("all",),
    update: bool = False,
    include_foundation: bool = False,
    adopt_foundation: bool = False,
) -> dict[str, Any]:
    """Install from the checkout the bootstrap script already resolved.

    The bootstrap script must clone the release to obtain this Core, so cloning
    again would both waste the transfer and open a window in which the tag moves
    between the two clones. Instead the checkout is re-verified locally: ``ref``
    must still resolve inside it, and it must be the commit that is checked out.
    """
    _validate_git_source(source)
    if not isinstance(ref, str) or not ref.strip() or ref.startswith("-"):
        raise DistributionError("Git ref must be a non-empty immutable tag or full commit")
    release_root = Path(checkout).expanduser().resolve()
    if not (release_root / ".git").exists():
        raise DistributionError(f"bootstrap checkout is not a Git checkout: {release_root}")
    _verify_checkout_source(release_root, source)
    commit = _resolve_immutable_git_ref(release_root, ref)
    head = _git(["rev-parse", "--verify", "HEAD^{commit}"], cwd=release_root)
    if head != commit:
        raise DistributionError(
            "bootstrap checkout does not have the requested immutable ref checked out"
        )
    # Release digests are recorded inside the release itself, so a modified
    # working tree that is re-signed still verifies. Requiring a clean tree is
    # what actually ties the installed files to the recorded commit.
    dirty = _git(["status", "--porcelain", "--untracked-files=normal"], cwd=release_root)
    if dirty:
        raise DistributionError(
            "bootstrap checkout has uncommitted changes; refusing to install files "
            "that do not match the recorded commit: "
            + "; ".join(sorted(dirty.splitlines())[:5])
        )
    _reject_moved_ref(project, source, ref, commit)
    return install_from_checkout(
        release_root,
        project,
        source=source,
        ref=ref,
        commit=commit,
        runtimes=runtimes,
        update=update,
        include_foundation=include_foundation,
        adopt_foundation=adopt_foundation,
    )


def plan_git_update(
    source: str,
    ref: str,
    project: str | Path,
    *,
    runtimes: Iterable[str] = ("all",),
    include_foundation: bool = False,
) -> dict[str, Any]:
    project_root = Path(project).expanduser().resolve()
    current = load_install_lock(project_root)
    if current is None:
        raise DistributionError("cannot plan an update before ISEKAI is installed")
    _validate_git_source(source)
    selected = _normalize_runtimes(runtimes)
    with tempfile.TemporaryDirectory(prefix="isekai-release-plan-") as temporary:
        checkout = Path(temporary) / "checkout"
        _git(["clone", "--quiet", "--no-checkout", source, str(checkout)])
        commit = _resolve_immutable_git_ref(checkout, ref)
        _git(["checkout", "--quiet", "--detach", commit], cwd=checkout)
        locked_source = current.get("source", {})
        if (
            locked_source.get("git") == source
            and locked_source.get("ref") == ref
            and locked_source.get("commit") not in {None, commit}
        ):
            raise DistributionError(
                "Git ref moved to a different commit; use a new immutable tag"
            )
        target = _verify_or_raise(checkout)

    def source_digest(entry: object) -> object:
        if not isinstance(entry, dict):
            return None
        return entry.get("source_digest", entry.get("digest"))

    def change(
        component: str,
        current_entry: object,
        target_entry: dict[str, Any],
        *,
        policy: str | None = None,
    ) -> dict[str, Any]:
        current_value = current_entry if isinstance(current_entry, dict) else {}
        from_version = current_value.get("version")
        to_version = target_entry.get("version")
        from_digest = source_digest(current_value)
        to_digest = source_digest(target_entry)
        result = {
            "component": component,
            "from": from_version,
            "to": to_version,
            "from_digest": from_digest,
            "to_digest": to_digest,
            "changed": from_version != to_version or from_digest != to_digest,
        }
        if policy is not None:
            result["policy"] = policy
        return result

    adapters = {item["id"]: item for item in target["adapters"]}
    changes = [change("core", current.get("core"), target["core"])]
    changes.extend(
        change(
            f"adapter:{runtime}",
            current.get("adapters", {}).get(runtime),
            adapters[runtime],
        )
        for runtime in selected
    )
    current_foundation = current.get("foundation")
    target_foundation = (
        target["foundation"] if include_foundation else current_foundation
    )
    if not isinstance(target_foundation, dict):
        target_foundation = {}
    changes.append(
        change(
            "foundation",
            current_foundation,
            target_foundation,
            policy="explicit" if include_foundation else "preserved",
        )
    )
    return {
        "ready": True,
        "project": str(project_root),
        "source": source,
        "ref": ref,
        "commit": commit,
        "current_release": current.get("release"),
        "target_release": target["version"],
        "protocol_version": target["protocol_version"],
        "changes": changes,
        "requires_confirmation": True,
        "new_conversation_required": bool({"codex", "claude"} & set(selected)),
    }
