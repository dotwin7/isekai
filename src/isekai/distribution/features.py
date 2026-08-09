from __future__ import annotations

from pathlib import Path
from typing import Any

from .marketplace import _replace_tree
from .release import MANAGED_ROOT, _component_root, _verified_tree_digest


def stage_feature_catalog(
    release_root: Path,
    staged: Path,
    manifest: dict[str, Any],
) -> dict[str, str]:
    source_entry = manifest["features"]
    source = _component_root(
        release_root,
        source_entry["path"],
        label="features.path",
    )
    target = staged / "features"
    _replace_tree(source, target)
    digest = _verified_tree_digest(
        target,
        source_entry["digest"],
        label="ISEKAI Feature Catalog",
        include_transients=True,
    )
    return {
        "id": str(source_entry["id"]),
        "version": str(source_entry["version"]),
        "path": f"{MANAGED_ROOT}/features",
        "source_digest": str(source_entry["digest"]),
        "digest": digest,
    }
