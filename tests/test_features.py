from __future__ import annotations

import json
from pathlib import Path

import pytest

import isekai.workflow.features as feature_module
from isekai.foundation import FoundationError
from isekai.runtime_contract import dispatch
from isekai.workflow import (
    feature_resources,
    load_feature_catalog,
    read_feature_resource,
    resolve_context,
)

from test_core_workflow import make_project


def test_feature_catalog_contains_active_ai_dlc() -> None:
    catalog = load_feature_catalog()
    features = {feature["id"]: feature for feature in catalog["features"]}

    assert catalog["type"] == "isekai-feature-catalog"
    assert catalog["catalog_digest"].startswith("sha256:")
    assert set(features) == {"ai-dlc"}
    assert features["ai-dlc"]["active"] is True
    assert features["ai-dlc"]["delivery"] == "core-bundled"
    assert features["ai-dlc"]["package_path"] == "ai-dlc/0.2.1"
    assert all(
        feature["authority"]
        == "cannot-expand-foundation-project-or-unit-authority"
        for feature in features.values()
    )


def test_context_receipt_binds_installed_isekai_feature_catalog(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)

    receipt = resolve_context(project)

    assert receipt["features"]["catalog_digest"] == (
        load_feature_catalog()["catalog_digest"]
    )


def test_feature_status_is_available_through_runtime_contract() -> None:
    result = dispatch("feature-status", {})

    assert result["action"] == "feature-status"
    assert {feature["id"] for feature in result["result"]["features"]} == {"ai-dlc"}


def test_feature_catalog_is_exposed_as_mcp_resources() -> None:
    catalog = load_feature_catalog()
    resources = feature_resources(catalog)
    uris = {resource["uri"] for resource in resources}

    assert "isekai://runtime/features" in uris
    assert "isekai://runtime/features/ai-dlc" in uris
    content = read_feature_resource(
        catalog,
        "isekai://runtime/features/ai-dlc",
    )
    value = json.loads(content["text"])
    assert value["id"] == "ai-dlc"
    assert value["kind"] == "isekai-feature"


def test_feature_catalog_is_managed_as_a_repository_distribution_component() -> None:
    root = Path(__file__).resolve().parents[1]
    source = json.loads((root / "features/catalog.json").read_text(encoding="utf-8"))
    entry = source["features"][0]

    assert entry == {
        "id": "ai-dlc",
        "version": "0.2.1",
        "manifest": "ai-dlc/0.2.1/feature.json",
    }
    assert (root / "features" / entry["manifest"]).is_file()


def test_unknown_feature_authority_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = load_feature_catalog()["features"][0]
    broken = {
        key: value
        for key, value in original.items()
        if key not in {"active", "feature_digest", "package_path"}
    }
    broken["authority"] = "feature-controls-core"
    package = tmp_path / broken["id"] / broken["version"]
    package.mkdir(parents=True)
    (package / "feature.json").write_text(
        json.dumps(broken) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "catalog.json").write_text(
        json.dumps(
            {
                "kind": "isekai-feature-source-catalog",
                "schema_version": "1.0.0",
                "control_protocol": "1.1.0",
                "features": [
                    {
                        "id": broken["id"],
                        "version": broken["version"],
                        "manifest": (
                            f"{broken['id']}/{broken['version']}/feature.json"
                        ),
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(feature_module, "FEATURE_ROOT", tmp_path)

    with pytest.raises(FoundationError, match="invalid authority"):
        load_feature_catalog()
