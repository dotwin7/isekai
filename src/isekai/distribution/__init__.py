from __future__ import annotations

"""Stable distribution API backed by release, install, and Git modules."""

from .git import (
    run_git as _git,
    reject_moved_ref as _reject_moved_ref,
    resolve_immutable_git_ref as _resolve_immutable_git_ref,
    validate_git_source as _validate_git_source,
    install_from_bootstrap_checkout,
    install_from_git,
    plan_git_update,
)
from .install import (
    adopt_foundation as _adopt_foundation,
    current_foundation_matches as _current_foundation_matches,
    doctor_install,
    install_from_checkout,
    rollback_install,
    verify_adapter_handshake,
)
from .lockfile import (
    installed_path as _installed_path,
    project_path_without_symlinks as _project_path_without_symlinks,
    load_install_lock,
)
from .execution_profile import apply_execution_profile, execution_profile_status
from .release import (
    DISTRIBUTION_SCHEMA_VERSION,
    LOCK_NAME,
    LOCK_SCHEMA_VERSION,
    MANAGED_ROOT,
    MANIFEST_PATH,
    PROTOCOL_VERSION,
    RUNTIMES,
    DistributionError,
    component_root as _component_root,
    normalize_runtimes as _normalize_runtimes,
    read_json as _read_json,
    safe_relative_path as _safe_relative_path,
    verify_or_raise as _verify_or_raise,
    write_json_atomic as _write_json_atomic,
    build_distribution_manifest,
    load_distribution_manifest,
    tree_digest,
    verify_distribution,
    write_distribution_manifest,
)
from .marketplace import (
    copy_managed_root as _copy_managed_root,
    replace_tree as _replace_tree,
)


__all__ = [
    "DistributionError",
    "apply_execution_profile",
    "build_distribution_manifest",
    "doctor_install",
    "execution_profile_status",
    "install_from_bootstrap_checkout",
    "install_from_checkout",
    "install_from_git",
    "load_distribution_manifest",
    "load_install_lock",
    "plan_git_update",
    "rollback_install",
    "tree_digest",
    "verify_adapter_handshake",
    "verify_distribution",
    "write_distribution_manifest",
]
