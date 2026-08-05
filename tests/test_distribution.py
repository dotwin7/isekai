from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from isekai.distribution import (
    DistributionError,
    build_distribution_manifest,
    doctor_install,
    install_from_checkout,
    install_from_git,
    load_install_lock,
    rollback_install,
    tree_digest,
    verify_adapter_handshake,
    verify_distribution,
    write_distribution_manifest,
)


ROOT = Path(__file__).resolve().parents[1]


def _project_with_foundation(tmp_path: Path) -> Path:
    project = tmp_path / "product"
    project.mkdir(parents=True)
    shutil.copytree(ROOT / "foundation", project / "foundation")
    (project / "project.json").write_text(
        json.dumps(
            {
                "id": "product",
                "kind": "project",
                "schema_version": "1.0.0",
                "version": "0.1.0",
                "foundation_path": "foundation",
                "profiles": ["software-delivery-profile"],
                "extensions": [],
                "maximum_agent_level": "L0",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return project


def _install(project: Path, checkout: Path = ROOT, *, commit: str = "a" * 40):
    return install_from_checkout(
        checkout,
        project,
        source="https://example.invalid/isekai.git",
        ref="v0.1.0",
        commit=commit,
        runtimes=("all",),
    )


def _copy_release(tmp_path: Path) -> Path:
    release = tmp_path / "release"
    shutil.copytree(
        ROOT,
        release,
        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache"),
    )
    return release


def _bump_release(release: Path, version: str) -> None:
    replacements = [
        release / "pyproject.toml",
        release / "src/isekai/__init__.py",
        release / "plugin/isekai/manifest.json",
        release / "plugin/isekai/runtimes/codex/.codex-plugin/plugin.json",
        release / "plugin/isekai/runtimes/claude/.claude-plugin/plugin.json",
        release / ".kiro/skills/isekai/SKILL.md",
        release / "plugin/isekai/runtimes/codex/skills/isekai/SKILL.md",
        release / "plugin/isekai/runtimes/claude/skills/isekai/SKILL.md",
    ]
    replacements.extend(path for path in (release / "foundation").rglob("*.json"))
    for path in replacements:
        content = path.read_text(encoding="utf-8").replace("0.1.0", version)
        path.write_text(content, encoding="utf-8")
    write_distribution_manifest(release)


def test_checked_in_distribution_manifest_matches_release_components() -> None:
    result = verify_distribution(ROOT)

    assert result["valid"] is True
    assert result["release"] == "0.1.0"
    assert build_distribution_manifest(ROOT) == json.loads(
        (ROOT / "distribution/release.json").read_text(encoding="utf-8")
    )


def test_project_install_is_pinned_idempotent_and_host_ready(tmp_path: Path) -> None:
    project = _project_with_foundation(tmp_path)

    first = _install(project)
    before = (project / "isekai.lock.json").read_bytes()
    second = _install(project)
    lock = load_install_lock(project)

    assert first["installed"] is True
    assert second["unchanged"] is True
    assert (project / "isekai.lock.json").read_bytes() == before
    assert lock is not None
    assert lock["source"]["ref"] == "v0.1.0"
    assert lock["source"]["commit"] == "a" * 40
    assert set(lock["adapters"]) == {"kiro", "claude", "codex"}
    assert (project / ".kiro/skills/isekai/SKILL.md").is_file()
    assert (
        project
        / ".isekai/marketplaces/codex/.agents/plugins/marketplace.json"
    ).is_file()
    assert (
        project
        / ".isekai/marketplaces/claude/.claude-plugin/marketplace.json"
    ).is_file()
    assert doctor_install(project)["ready"] is True

    with pytest.raises(DistributionError, match="adapter version does not match project lock"):
        verify_adapter_handshake("codex", "0.2.0", "1.0.0", project)

    completed = subprocess.run(
        [str(project / ".isekai/bin/isekai"), "plugin", "compatibility"],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["core_version"] == "0.1.0"


def test_installed_launcher_initializes_project_from_locked_foundation(
    tmp_path: Path,
) -> None:
    project = tmp_path / "empty-product"
    project.mkdir()
    result = _install(project)

    completed = subprocess.run(
        [str(project / ".isekai/bin/isekai"), "init", "--path", str(project)],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )
    manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))

    assert "next_action" in result
    assert completed.returncode == 0, completed.stderr
    assert manifest["foundation_path"] == ".isekai/foundations/0.1.0"
    assert doctor_install(project)["ready"] is True


def test_doctor_and_update_fail_closed_after_managed_file_tampering(
    tmp_path: Path,
) -> None:
    project = _project_with_foundation(tmp_path)
    _install(project)
    core_file = project / ".isekai/runtime/isekai/__init__.py"
    core_file.write_text(core_file.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")

    health = doctor_install(project)

    assert health["ready"] is False
    assert "core digest mismatch" in health["issues"]
    with pytest.raises(DistributionError, match="modified or are incomplete"):
        install_from_checkout(
            ROOT,
            project,
            source="https://example.invalid/isekai.git",
            ref="v0.1.1",
            commit="b" * 40,
            runtimes=("codex",),
            update=True,
        )


def test_update_preserves_foundation_and_rollback_restores_previous_release(
    tmp_path: Path,
) -> None:
    project = _project_with_foundation(tmp_path)
    _install(project)
    old_lock = load_install_lock(project)
    assert old_lock is not None
    old_kiro_digest = tree_digest(project / ".kiro/skills/isekai")

    release = _copy_release(tmp_path)
    _bump_release(release, "0.1.1")
    updated = install_from_checkout(
        release,
        project,
        source="https://example.invalid/isekai.git",
        ref="v0.1.1",
        commit="b" * 40,
        runtimes=("all",),
        update=True,
    )
    updated_lock = load_install_lock(project)

    assert updated["updated"] is True
    assert updated_lock is not None
    assert updated_lock["release"] == "0.1.1"
    assert updated_lock["core"]["version"] == "0.1.1"
    assert updated_lock["foundation"]["version"] == "0.1.0"
    assert doctor_install(project)["ready"] is True

    rolled_back = rollback_install(project)
    restored = load_install_lock(project)

    assert rolled_back["rolled_back"] is True
    assert restored == old_lock
    assert tree_digest(project / ".kiro/skills/isekai") == old_kiro_digest
    assert doctor_install(project)["ready"] is True


def test_install_from_immutable_local_git_tag_records_resolved_commit(
    tmp_path: Path,
) -> None:
    release = _copy_release(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=release, check=True)
    subprocess.run(["git", "add", "."], cwd=release, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ISEKAI Test",
            "-c",
            "user.email=isekai@example.invalid",
            "commit",
            "-qm",
            "release",
        ],
        cwd=release,
        check=True,
    )
    subprocess.run(["git", "tag", "v0.1.0"], cwd=release, check=True)
    expected_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=release,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    project = _project_with_foundation(tmp_path / "target")

    result = install_from_git(
        str(release),
        "v0.1.0",
        project,
        runtimes=("kiro",),
    )

    assert result["commit"] == expected_commit
    assert load_install_lock(project)["source"]["commit"] == expected_commit
