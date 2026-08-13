from __future__ import annotations

from pathlib import Path
from typing import Any

from .marketplace import replace_tree as _replace_tree
from .release import (
    MANAGED_ROOT,
    component_root as _component_root,
    verified_tree_digest as _verified_tree_digest,
)


def stage_catalog(
    release_root: Path,
    staged: Path,
    manifest: dict[str, Any],
) -> dict[str, str]:
    source_entry = manifest["catalog"]
    source = _component_root(
        release_root,
        source_entry["path"],
        label="catalog.path",
    )
    target = staged / "catalog"
    _replace_tree(source, target)
    digest = _verified_tree_digest(
        target,
        source_entry["digest"],
        label="ISEKAI Catalog",
        include_transients=True,
    )
    return {
        "id": str(source_entry["id"]),
        "version": str(source_entry["version"]),
        "path": f"{MANAGED_ROOT}/catalog",
        "source_digest": str(source_entry["digest"]),
        "digest": digest,
    }
