from __future__ import annotations

"""Stable distribution API backed by release, marketplace, install, and Git modules."""

from .git import (
    _git,
    _reject_moved_ref,
    _resolve_immutable_git_ref,
    _validate_git_source,
    install_from_bootstrap_checkout,
    install_from_git,
    plan_git_update,
)
from .install import (
    _adopt_foundation,
    _current_foundation_matches,
    _installed_path,
    _project_path_without_symlinks,
    doctor_install,
    install_from_checkout,
    load_install_lock,
    rollback_install,
    verify_adapter_handshake,
)
from .release import (
    DISTRIBUTION_SCHEMA_VERSION,
    LOCK_NAME,
    LOCK_SCHEMA_VERSION,
    MANAGED_ROOT,
    MANIFEST_PATH,
    PLUGIN_ID,
    PROTOCOL_VERSION,
    RUNTIMES,
    DistributionError,
    _component_root,
    _normalize_runtimes,
    _read_json,
    _safe_relative_path,
    _verify_or_raise,
    _write_json_atomic,
    build_distribution_manifest,
    load_distribution_manifest,
    tree_digest,
    verify_distribution,
    write_distribution_manifest,
)
from .marketplace import (
    _copy_managed_root,
    _replace_tree,
)


__all__ = [
    "DistributionError",
    "build_distribution_manifest",
    "doctor_install",
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
