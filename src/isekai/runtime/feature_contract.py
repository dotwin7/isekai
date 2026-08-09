from __future__ import annotations

from typing import Any


FEATURE_MODEL = {
    "unit": "versioned-isekai-feature",
    "distribution": "core-bundled-or-feature-package",
    "exposure": "project-local-core-mcp-control-plane",
    "context_binding": "sha256-feature-catalog-and-package-digests",
    "project_ownership": "not-a-product-extension",
    "permission_effect": "cannot-expand-foundation-project-or-unit-authority",
}


def feature_model_issues(value: Any) -> list[str]:
    if value != FEATURE_MODEL:
        return ["compatibility matrix has an invalid feature_model"]
    return []
