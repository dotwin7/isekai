from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from isekai.foundation import FoundationError
from isekai.workflow.errors import WorkflowError
from isekai.workflow.session import (
    SessionError,
    build_session,
    discover_project,
    inception_session,
)
from isekai.workflow import initialize_project, initialize_unit, resolve_context


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


def test_initialize_unit_write_failure_leaves_no_partial_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import isekai.catalog.ai_dlc.unit.initialization as initialization

    project_root = project_root_with_foundation(tmp_path)
    project = initialize_project(project_root)
    real_write = initialization._write_json
    calls = 0

    def fail_second_write(path: Path, value: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("forced Unit write failure")
        real_write(path, value)

    monkeypatch.setattr(initialization, "_write_json", fail_second_write)

    with pytest.raises(OSError, match="forced Unit write failure"):
        initialize_unit(project, "Atomic Unit")

    assert list((project_root / "units").iterdir()) == []


def test_initialize_unit_allows_repeated_titles_on_the_same_day(
    tmp_path: Path,
) -> None:
    project_root = project_root_with_foundation(tmp_path)
    project = initialize_project(project_root)

    first = initialize_unit(project, "Repeated Incident")
    second = initialize_unit(project, "Repeated Incident")

    assert first != second
    assert first.is_dir()
    assert second.is_dir()
    assert json.loads((first / "unit.json").read_text(encoding="utf-8"))["id"] != json.loads(
        (second / "unit.json").read_text(encoding="utf-8")
    )["id"]


def test_inception_does_not_select_from_existing_units(tmp_path: Path) -> None:
    project_root = project_root_with_foundation(tmp_path)
    project = initialize_project(project_root)
    first = initialize_unit(project, "First Existing Unit")
    second = initialize_unit(project, "Second Existing Unit")

    session = inception_session(project)

    assert session["unit"] is None
    assert {candidate["title"] for candidate in session["unit_candidate_details"]} == {
        "First Existing Unit",
        "Second Existing Unit",
    }
    assert session["inception"]["decision_required"] is True


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
    canonical = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")

    assert "## Project bootstrap과 discovery" in canonical
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


def test_explicit_project_manifest_must_use_the_canonical_filename(
    tmp_path: Path,
) -> None:
    project_root = project_root_with_foundation(tmp_path)
    manifest = initialize_project(
        project_root,
        profiles=["software-delivery-profile"],
    )
    alias = project_root / "alias.json"
    alias.write_bytes(manifest.read_bytes())

    with pytest.raises(FoundationError, match="must be named project.json"):
        resolve_context(alias)


@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
def test_project_manifest_must_be_a_single_link_regular_file(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    project_root = project_root_with_foundation(tmp_path)
    manifest = initialize_project(
        project_root,
        profiles=["software-delivery-profile"],
    )
    external = tmp_path / "external-project.json"
    manifest.rename(external)
    if alias_kind == "symlink":
        manifest.symlink_to(external)
    else:
        os.link(external, manifest)

    with pytest.raises(FoundationError, match="single-link|symlink"):
        resolve_context(project_root)


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


def test_context_and_unit_init_accept_project_root_or_nested_directory(
    tmp_path: Path,
) -> None:
    project_root = project_root_with_foundation(tmp_path)
    manifest = initialize_project(
        project_root,
        project_id="discovered-project",
        profiles=["software-delivery-profile"],
    )
    nested = project_root / "src/package"
    nested.mkdir(parents=True)

    assert resolve_context(project_root)["source_manifest"] == str(manifest)
    unit = initialize_unit(nested, "Discovered Project")

    assert unit.parent == project_root / "units"


def test_unknown_agent_level_is_rejected_fail_closed(tmp_path: Path) -> None:
    project_root = project_root_with_foundation(tmp_path)

    with pytest.raises(WorkflowError, match="maximum_agent_level"):
        initialize_project(project_root, maximum_agent_level="L3")

    assert not (project_root / "project.json").exists()
    assert not (project_root / "units").exists()


def test_session_rejects_a_unit_from_another_project(tmp_path: Path) -> None:
    first_root = project_root_with_foundation(tmp_path, "first")
    first = initialize_project(
        first_root,
        project_id="first-project",
        profiles=["software-delivery-profile"],
    )
    second_root = project_root_with_foundation(tmp_path, "second")
    second = initialize_project(
        second_root,
        project_id="second-project",
        profiles=["software-delivery-profile"],
    )
    foreign_unit = initialize_unit(second, "Foreign Unit")

    with pytest.raises(SessionError, match="project_id"):
        build_session(first, foreign_unit)


def test_session_rejects_a_renamed_unit_directory(tmp_path: Path) -> None:
    project = project_root_with_foundation(tmp_path)
    manifest = initialize_project(
        project,
        project_id="renamed-unit-project",
        profiles=["software-delivery-profile"],
    )
    unit = initialize_unit(manifest, "Renamed Unit")
    renamed = unit.with_name("renamed-unit")
    unit.rename(renamed)

    with pytest.raises(SessionError, match="canonical Unit id"):
        build_session(manifest, renamed)
