from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from isekai.foundation import FoundationError
from isekai.session import SessionError, discover_project
from isekai.workflow import initialize_project, resolve_context


ROOT = Path(__file__).resolve().parents[1]


def project_root_with_foundation(tmp_path: Path, name: str = "new-project") -> Path:
    project_root = tmp_path / name
    project_root.mkdir()
    shutil.copytree(ROOT / "foundation", project_root / "foundation")
    return project_root


def write_candidate(path: Path, project_id: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": project_id}) + "\n", encoding="utf-8")
    return path.resolve()


def test_initialize_project_creates_valid_manifest_and_units_without_overwrite(
    tmp_path: Path,
) -> None:
    project_root = project_root_with_foundation(tmp_path)

    manifest = initialize_project(
        project_root,
        project_id="internal-product",
        profiles=["software-delivery-profile"],
        document_language="en",
    )

    assert manifest == project_root / "project.json"
    assert (project_root / "units").is_dir()
    context = resolve_context(manifest)
    assert context["project_id"] == "internal-product"
    assert context["profiles"] == ["software-delivery-profile"]
    assert context["document_language"] == "en"

    before = manifest.read_bytes()
    with pytest.raises(FileExistsError, match="already exists"):
        initialize_project(project_root, project_id="replacement")
    assert manifest.read_bytes() == before


def test_initialize_project_preflight_failure_leaves_no_artifacts(tmp_path: Path) -> None:
    project_root = project_root_with_foundation(tmp_path)

    with pytest.raises(FoundationError, match="invalid profile"):
        initialize_project(project_root, profiles=["missing-profile"])

    assert not (project_root / "project.json").exists()
    assert not (project_root / "units").exists()


def test_discover_project_uses_single_descendant_and_lists_ambiguous_candidates(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = write_candidate(workspace / "services/first/project.json", "first")

    assert discover_project(workspace) == first

    second = write_candidate(workspace / "services/second/project.json", "second")
    with pytest.raises(SessionError, match="multiple project manifests") as exc_info:
        discover_project(workspace)
    message = str(exc_info.value)
    assert str(first) in message
    assert str(second) in message
    assert "pass --project explicitly" in message


def test_discover_project_prefers_direct_and_ancestor_manifests(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    direct = write_candidate(workspace / "project.json", "workspace")
    write_candidate(workspace / "services/child/project.json", "child")

    assert discover_project(workspace) == direct
    nested = workspace / "src/package"
    nested.mkdir(parents=True)
    assert discover_project(nested) == direct


def test_discover_project_ignores_generated_directories_and_suggests_init(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_candidate(workspace / "node_modules/dependency/project.json", "dependency")
    write_candidate(workspace / "units/old/project.json", "old")

    with pytest.raises(SessionError, match="isekai init"):
        discover_project(workspace)


def test_canonical_docs_define_project_bootstrap_and_persistence_contract() -> None:
    canonical = (ROOT / "docs/isekai.md").read_text(encoding="utf-8")

    assert "### 4.1 Project bootstrap과 discovery" in canonical
    assert "direct current directory → nearest ancestor → filtered descendants" in canonical
    assert "project-root/units/" in canonical
    assert "units/**/evidence/raw/" in canonical
    assert "기존 `project.json`을 덮어쓰지 않으며" in canonical


def test_initialize_project_allows_shared_foundation_outside_project(
    tmp_path: Path,
) -> None:
    shared_foundation = tmp_path / "shared-foundation"
    shutil.copytree(ROOT / "foundation", shared_foundation)
    project_root = tmp_path / "product"
    project_root.mkdir()

    manifest = initialize_project(
        project_root,
        foundation_path="../shared-foundation",
        profiles=["software-delivery-profile"],
    )

    context = resolve_context(manifest)
    assert context["foundation_id"] == "isekai-foundation"


def test_initialize_project_postflight_failure_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import isekai.workflow as workflow

    project_root = project_root_with_foundation(tmp_path)

    def fail_postflight(path: str | Path):
        raise FoundationError(f"postflight failure: {path}")

    monkeypatch.setattr(workflow, "load_project", fail_postflight)
    with pytest.raises(FoundationError, match="postflight failure"):
        initialize_project(project_root, profiles=["software-delivery-profile"])

    assert not (project_root / "project.json").exists()
    assert not (project_root / "units").exists()


def test_discover_project_uses_nearest_ancestor_in_nested_projects(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    write_candidate(workspace / "project.json", "workspace")
    nearest = write_candidate(workspace / "services/child/project.json", "child")
    nested = workspace / "services/child/src/package"
    nested.mkdir(parents=True)

    assert discover_project(nested) == nearest
