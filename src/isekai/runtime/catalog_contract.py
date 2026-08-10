from __future__ import annotations

from typing import Any


CATALOG_MODEL = {
    "unit": "versioned-isekai-catalog-entry",
    "distribution": "core-bundled-or-catalog-package",
    "exposure": "project-local-core-mcp-control-plane",
    "context_binding": "sha256-catalog-and-package-digests",
    "project_ownership": "not-a-product-extension",
    "permission_effect": "cannot-expand-foundation-project-or-unit-authority",
}


def catalog_model_issues(value: Any) -> list[str]:
    if value != CATALOG_MODEL:
        return ["compatibility matrix has an invalid catalog_model"]
    return []
