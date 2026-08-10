from __future__ import annotations

import json
from pathlib import Path

import pytest

import isekai.workflow.catalog as catalog_module
from isekai.foundation import FoundationError
from isekai.runtime_contract import dispatch
from isekai.workflow import (
    catalog_resources,
    load_catalog,
    read_catalog_resource,
    resolve_context,
)

from test_core_workflow import make_project


def test_catalog_contains_active_ai_dlc() -> None:
    catalog = load_catalog()
    entries = {e["id"]: e for e in catalog["entries"]}

    assert catalog["type"] == "isekai-catalog"
    assert catalog["catalog_digest"].startswith("sha256:")
    assert set(entries) == {"ai-dlc"}
    assert entries["ai-dlc"]["active"] is True
    assert entries["ai-dlc"]["delivery"] == "core-bundled"
    assert entries["ai-dlc"]["package_path"] == "ai-dlc/0.3.0"
    assert all(
        entry["authority"]
        == "cannot-expand-foundation-project-or-unit-authority"
        for entry in entries.values()
    )


def test_context_receipt_binds_installed_isekai_catalog(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)

    receipt = resolve_context(project)

    assert receipt["catalog"]["catalog_digest"] == (
        load_catalog()["catalog_digest"]
    )


def test_feature_status_is_available_through_runtime_contract() -> None:
    result = dispatch("catalog-status", {})

    assert result["action"] == "catalog-status"
    assert {e["id"] for e in result["result"]["entries"]} == {"ai-dlc"}


def test_catalog_is_exposed_as_mcp_resources() -> None:
    catalog = load_catalog()
    resources = catalog_resources(catalog)
    uris = {resource["uri"] for resource in resources}

    assert "isekai://runtime/catalog" in uris
    assert "isekai://runtime/catalog/ai-dlc" in uris
    content = read_catalog_resource(
        catalog,
        "isekai://runtime/catalog/ai-dlc",
    )
    value = json.loads(content["text"])
    assert value["id"] == "ai-dlc"
    assert value["kind"] == "isekai-catalog-entry"


def test_catalog_is_managed_as_a_repository_distribution_component() -> None:
    root = Path(__file__).resolve().parents[1]
    source = json.loads((root / "catalog/catalog.json").read_text(encoding="utf-8"))
    entry = source["entries"][0]

    assert entry == {
        "id": "ai-dlc",
        "version": "0.3.0",
        "manifest": "ai-dlc/0.3.0/manifest.json",
    }
    assert (root / "catalog" / entry["manifest"]).is_file()


def test_feature_scaffold_template_contains_required_fields() -> None:
    root = Path(__file__).resolve().parents[1]
    template_path = root / "catalog/_template/manifest.json.example"

    assert template_path.is_file(), "scaffold template must exist"
    content = json.loads(template_path.read_text(encoding="utf-8"))
    required = {
        "id",
        "kind",
        "schema_version",
        "version",
        "status",
        "title",
        "description",
        "control_protocol",
        "delivery",
        "actions",
        "resources",
        "authority",
    }
    assert required <= set(content), f"template missing: {required - set(content)}"
    assert content["kind"] == "isekai-catalog-entry"
    assert content["authority"] == "cannot-expand-foundation-project-or-unit-authority"


def test_catalog_loader_ignores_underscore_prefixed_directories() -> None:
    catalog = load_catalog()
    entry_ids = {e["id"] for e in catalog["entries"]}

    assert "_template" not in entry_ids
    assert all(not fid.startswith("_") for fid in entry_ids)


def test_unknown_feature_authority_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = load_catalog()["entries"][0]
    broken = {
        key: value
        for key, value in original.items()
        if key not in {"active", "entry_digest", "package_path"}
    }
    broken["authority"] = "entry-controls-core"
    package = tmp_path / broken["id"] / broken["version"]
    package.mkdir(parents=True)
    (package / "manifest.json").write_text(
        json.dumps(broken) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "catalog.json").write_text(
        json.dumps(
            {
                "kind": "isekai-source-catalog",
                "schema_version": "1.0.0",
                "control_protocol": "1.2.0",
                "entries": [
                    {
                        "id": broken["id"],
                        "version": broken["version"],
                        "manifest": (
                            f"{broken['id']}/{broken['version']}/manifest.json"
                        ),
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog_module, "CATALOG_ROOT", tmp_path)

    with pytest.raises(FoundationError, match="invalid authority"):
        load_catalog()
