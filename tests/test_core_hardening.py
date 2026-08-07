from __future__ import annotations

import json
from pathlib import Path

import pytest

from isekai.cli import DIRECT_PLUGIN_ACTIONS, _parser, main
from isekai.distribution import DistributionError, install_from_git, plan_git_update
from isekai.foundation import load_foundation
from isekai.jsonio import write_json_atomic
from isekai.session import _descendant_project_candidates, update_checkpoint
from isekai.workflow import authorize_action, initialize_unit

from test_core_workflow import ROOT, make_project
from test_execution_envelope import approve_inception, envelope_stages


def _top_level_commands() -> set[str]:
    actions = _parser()._subparsers._group_actions  # type: ignore[union-attr]
    return set(actions[0].choices)


def test_no_top_level_command_is_shadowed_by_a_plugin_alias() -> None:
    # A top-level command sharing a name with a plugin action is unreachable:
    # main() rewrites the argument list before argparse ever sees it.
    assert _top_level_commands() & DIRECT_PLUGIN_ACTIONS == set()


def test_every_direct_alias_reaches_its_plugin_action() -> None:
    plugin_actions = set(
        _parser()._subparsers._group_actions[0]  # type: ignore[union-attr]
        .choices["plugin"]
        ._subparsers._group_actions[0]
        .choices
    )
    assert DIRECT_PLUGIN_ACTIONS <= plugin_actions


def test_direct_route_alias_runs_the_plugin_contract(capsys) -> None:
    assert main(["route", "--change", "none"]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["action"] == "route"
    assert output["result"]["route"] == "query"


def test_release_readiness_does_not_repeat_blockers(tmp_path: Path) -> None:
    import shutil

    foundation_root = tmp_path / "foundation"
    shutil.copytree(ROOT / "foundation", foundation_root)
    broken = foundation_root / "evaluations/dod.json"
    asset = json.loads(broken.read_text(encoding="utf-8"))
    case = asset["content"]["cases"][0]
    case["expected"] = "fail" if case["expected"] == "pass" else "pass"
    write_json_atomic(broken, asset)

    blockers = load_foundation(foundation_root).readiness()["blockers"]

    assert len(blockers) == len(set(blockers))
    assert sum("dod-evaluation did not pass" in item for item in blockers) == 1


def test_git_source_rejects_transport_helpers(tmp_path: Path) -> None:
    project = tmp_path / "product"
    project.mkdir()

    for source in ("ext::sh -c whoami", "transport::payload"):
        with pytest.raises(DistributionError, match="transport helper"):
            install_from_git(source, "v0.1.0", project)


def test_update_plan_rejects_transport_helper_sources(tmp_path: Path) -> None:
    project = tmp_path / "product"
    project.mkdir()
    write_json_atomic(
        project / "isekai.lock.json",
        {"schema_version": "1.0.0", "release": "0.1.0", "adapters": {}},
    )

    with pytest.raises(DistributionError, match="transport helper"):
        plan_git_update("ext::sh -c whoami", "v0.1.0", project)


def _tagged_checkout(tmp_path: Path) -> Path:
    import shutil
    import subprocess

    from isekai.distribution import write_distribution_manifest

    release = tmp_path / "release"
    shutil.copytree(
        ROOT,
        release,
        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache"),
    )
    write_distribution_manifest(release)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid"]
    subprocess.run(["git", "init", "-q"], cwd=release, check=True)
    subprocess.run(["git", "add", "-A"], cwd=release, check=True)
    subprocess.run([*git, "commit", "-qm", "release"], cwd=release, check=True)
    subprocess.run(["git", "tag", "v9.9.9"], cwd=release, check=True)
    return release


def test_bootstrap_checkout_install_requires_a_clean_tree(tmp_path: Path) -> None:
    from isekai.distribution import install_from_bootstrap_checkout

    release = _tagged_checkout(tmp_path)
    project = tmp_path / "product"
    project.mkdir()
    # Release digests live inside the release, so re-signing a tampered tree
    # keeps it self-consistent. Only the clean-tree check ties the installed
    # files to the commit recorded in the lock.
    (release / "src/isekai/cli/__init__.py").write_text(
        (release / "src/isekai/cli/__init__.py").read_text(encoding="utf-8")
        + "# injected\n",
        encoding="utf-8",
    )
    from isekai.distribution import write_distribution_manifest

    write_distribution_manifest(release)

    with pytest.raises(DistributionError, match="uncommitted changes"):
        install_from_bootstrap_checkout(
            release, str(release), "v9.9.9", project, runtimes=("kiro",)
        )
    assert not (project / "isekai.lock.json").exists()


def test_bootstrap_checkout_install_rejects_a_mismatched_ref(tmp_path: Path) -> None:
    from isekai.distribution import install_from_bootstrap_checkout

    release = _tagged_checkout(tmp_path)
    project = tmp_path / "product"
    project.mkdir()

    with pytest.raises(DistributionError, match="immutable tag or full commit"):
        install_from_bootstrap_checkout(
            release, str(release), "main", project, runtimes=("kiro",)
        )


def test_bootstrap_checkout_install_rejects_a_mismatched_source(
    tmp_path: Path,
) -> None:
    from isekai.distribution import install_from_bootstrap_checkout

    release = _tagged_checkout(tmp_path)
    project = tmp_path / "product"
    project.mkdir()

    with pytest.raises(DistributionError, match="origin matching|origin does not match"):
        install_from_bootstrap_checkout(
            release,
            "https://trusted.example/isekai.git",
            "v9.9.9",
            project,
            runtimes=("kiro",),
        )

    assert not (project / "isekai.lock.json").exists()


def _wide_open_unit(project: Path, title: str, output: Path | None = None) -> Path:
    from isekai.workflow import propose_execution_envelope

    unit = initialize_unit(project, title, output or project.parent / "units")
    propose_execution_envelope(
        unit,
        scope=["**"],
        stages=envelope_stages(),
        allowed_actions=["read", "edit", "test"],
        forbidden_actions=["remote"],
        max_iterations=5,
        proposed_by="planner-agent",
    )
    approve_inception(unit)
    return unit


def test_unit_stored_outside_the_project_still_edits_project_files(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    unit = _wide_open_unit(project, "External Unit", tmp_path / "external-units")

    # Its own control artifacts are unreachable as Project-relative targets, so
    # there is nothing to protect and ordinary edits must keep working.
    assert authorize_action(unit, action="edit", target="src/main.py")["allowed"] is True


def test_control_artifacts_of_any_unit_are_protected(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    active = _wide_open_unit(project, "Active Unit")
    sibling = initialize_unit(project, "Sibling Unit", project.parent / "units")

    own = authorize_action(
        active,
        action="edit",
        target=str((active / "decisions.json").relative_to(project.parent)),
    )
    # A sibling Unit's ledger is just as much a control artifact as this one's.
    other = authorize_action(
        active,
        action="edit",
        target=str((sibling / "decisions.json").relative_to(project.parent)),
    )
    ordinary = authorize_action(
        active,
        action="edit",
        target=str((sibling / "requirements.md").relative_to(project.parent)),
    )
    protected_nested = [
        active / "checkpoint.json",
        active / "evaluations/criteria.json",
        active / "evidence/verification.json",
        active / "evidence/records/EVD-20260807000000000000.json",
    ]
    protected_results = [
        authorize_action(
            active,
            action="edit",
            target=str(path.relative_to(project.parent)),
        )
        for path in protected_nested
    ]

    assert own["allowed"] is False and "control artifact" in own["reason"]
    assert other["allowed"] is False and "control artifact" in other["reason"]
    assert all(
        result["allowed"] is False and "control artifact" in result["reason"]
        for result in protected_results
    )
    assert ordinary["allowed"] is True


def test_edit_authorization_rejects_existing_directory_targets(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    active = _wide_open_unit(project, "Directory Protection")

    unit_directory = authorize_action(
        active,
        action="edit",
        target=str(active.relative_to(project.parent)),
    )
    units_directory = authorize_action(active, action="edit", target="units")
    ordinary_file = authorize_action(active, action="edit", target="src/main.py")

    assert unit_directory["allowed"] is False
    assert units_directory["allowed"] is False
    assert "Directory targets" in unit_directory["reason"]
    assert "Directory targets" in units_directory["reason"]
    assert ordinary_file["allowed"] is True


def test_project_discovery_prunes_excluded_trees(tmp_path: Path) -> None:
    (tmp_path / "workspace/app").mkdir(parents=True)
    write_json_atomic(tmp_path / "workspace/app/project.json", {"id": "app"})
    vendored = tmp_path / "workspace/node_modules/pkg"
    vendored.mkdir(parents=True)
    write_json_atomic(vendored / "project.json", {"id": "vendored"})

    found = _descendant_project_candidates(tmp_path / "workspace")

    assert found == [(tmp_path / "workspace/app/project.json").resolve()]


def test_checkpoint_is_written_atomically(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Atomic Checkpoint", project.parent / "units")

    update_checkpoint(
        unit,
        completed=["inception"],
        pending=["construction"],
        blocked_by=[],
        next_action="continue construction",
    )

    checkpoint = json.loads((unit / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["next_action"] == "continue construction"
    # An interrupted write leaves its temporary file behind, never a torn record.
    assert not [path for path in unit.iterdir() if path.name.endswith(".tmp")]


def test_atomic_writer_leaves_no_partial_file_on_failure(tmp_path: Path) -> None:
    target = tmp_path / "record.json"
    write_json_atomic(target, {"ok": True})

    with pytest.raises(TypeError):
        write_json_atomic(target, {"bad": object()})

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert list(tmp_path.iterdir()) == [target]
