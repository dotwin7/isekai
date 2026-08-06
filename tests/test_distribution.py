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


def test_later_kiro_install_refuses_unmanaged_skill(tmp_path: Path) -> None:
    project = _project_with_foundation(tmp_path)
    common = {
        "source": "https://example.invalid/isekai.git",
        "ref": "v0.1.0",
        "commit": "a" * 40,
    }
    install_from_checkout(ROOT, project, runtimes=("codex",), **common)
    kiro = project / ".kiro/skills/isekai"
    kiro.mkdir(parents=True)
    marker = kiro / "UNMANAGED.txt"
    marker.write_text("preserve me", encoding="utf-8")

    with pytest.raises(DistributionError, match="unmanaged .kiro/skills/isekai"):
        install_from_checkout(ROOT, project, runtimes=("kiro",), **common)

    assert marker.read_text(encoding="utf-8") == "preserve me"
    assert set(load_install_lock(project)["adapters"]) == {"codex"}


def test_rollback_removes_kiro_added_after_codex_only_install(tmp_path: Path) -> None:
    project = _project_with_foundation(tmp_path)
    common = {
        "source": "https://example.invalid/isekai.git",
        "ref": "v0.1.0",
        "commit": "a" * 40,
    }
    install_from_checkout(ROOT, project, runtimes=("codex",), **common)
    install_from_checkout(ROOT, project, runtimes=("kiro",), **common)
    kiro = project / ".kiro/skills/isekai"
    assert kiro.is_dir()

    rollback_install(project)
    restored = load_install_lock(project)

    assert restored is not None
    assert set(restored["adapters"]) == {"codex"}
    assert not kiro.exists()
    assert doctor_install(project)["ready"] is True


def test_post_install_failure_restores_new_project_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from isekai import distribution as distribution_module

    project = _project_with_foundation(tmp_path)
    project_before = (project / "project.json").read_bytes()
    monkeypatch.setattr(
        distribution_module,
        "doctor_install",
        lambda _project: {"ready": False, "issues": ["forced postflight failure"]},
    )

    with pytest.raises(DistributionError, match="forced postflight failure"):
        install_from_checkout(
            ROOT,
            project,
            source="https://example.invalid/isekai.git",
            ref="v0.1.0",
            commit="a" * 40,
            runtimes=("kiro",),
        )

    assert (project / "project.json").read_bytes() == project_before
    assert not (project / ".isekai").exists()
    assert not (project / ".kiro/skills/isekai").exists()
    assert not (project / "isekai.lock.json").exists()


def test_rollback_failure_restores_current_project_lock_and_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from isekai import distribution as distribution_module

    project = _project_with_foundation(tmp_path)
    _install(project)
    release = _copy_release(tmp_path)
    _bump_release(release, "0.1.1")
    install_from_checkout(
        release,
        project,
        source="https://example.invalid/isekai.git",
        ref="v0.1.1",
        commit="b" * 40,
        runtimes=("all",),
        update=True,
        include_foundation=True,
        adopt_foundation=True,
    )
    project_before = (project / "project.json").read_bytes()
    lock_before = (project / "isekai.lock.json").read_bytes()
    core_before = tree_digest(project / ".isekai/runtime/isekai")
    kiro_before = tree_digest(project / ".kiro/skills/isekai")
    original_write = distribution_module._write_json_atomic

    def fail_previous_lock(path: Path, value: dict[str, object]) -> None:
        if path == project / "isekai.lock.json" and value.get("release") == "0.1.0":
            raise OSError("forced rollback lock failure")
        original_write(path, value)

    monkeypatch.setattr(distribution_module, "_write_json_atomic", fail_previous_lock)

    with pytest.raises(OSError, match="forced rollback lock failure"):
        rollback_install(project)

    assert (project / "project.json").read_bytes() == project_before
    assert (project / "isekai.lock.json").read_bytes() == lock_before
    assert tree_digest(project / ".isekai/runtime/isekai") == core_before
    assert tree_digest(project / ".kiro/skills/isekai") == kiro_before
    assert doctor_install(project)["ready"] is True


def test_git_install_rejects_branch_and_abbreviated_commit_refs(tmp_path: Path) -> None:
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
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=release,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=release,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    project = _project_with_foundation(tmp_path / "target")

    with pytest.raises(DistributionError, match="immutable tag or full commit"):
        install_from_git(str(release), branch, project, runtimes=("kiro",))
    with pytest.raises(DistributionError, match="immutable tag or full commit"):
        install_from_git(str(release), commit[:12], project, runtimes=("kiro",))

    result = install_from_git(str(release), commit, project, runtimes=("kiro",))
    assert result["commit"] == commit


def test_update_plan_reports_source_digest_changes(tmp_path: Path) -> None:
    from isekai.distribution import plan_git_update

    project = _project_with_foundation(tmp_path / "current")
    _install(project)
    current = load_install_lock(project)
    assert current is not None

    release = _copy_release(tmp_path)
    _bump_release(release, "0.1.1")
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
    subprocess.run(["git", "tag", "v0.1.1"], cwd=release, check=True)
    target_manifest = json.loads(
        (release / "distribution/release.json").read_text(encoding="utf-8")
    )

    plan = plan_git_update(
        str(release),
        "v0.1.1",
        project,
        runtimes=("codex",),
    )
    changes = {item["component"]: item for item in plan["changes"]}

    assert changes["core"] == {
        "component": "core",
        "from": "0.1.0",
        "to": "0.1.1",
        "from_digest": current["core"]["source_digest"],
        "to_digest": target_manifest["core"]["digest"],
        "changed": True,
    }
    assert changes["adapter:codex"]["from_digest"] == current["adapters"][
        "codex"
    ]["source_digest"]
    assert changes["adapter:codex"]["to_digest"] == next(
        item["digest"]
        for item in target_manifest["adapters"]
        if item["id"] == "codex"
    )
    assert changes["adapter:codex"]["changed"] is True
    assert changes["foundation"]["from_digest"] == current["foundation"]["digest"]
    assert changes["foundation"]["to_digest"] == current["foundation"]["digest"]
    assert changes["foundation"]["changed"] is False
    assert changes["foundation"]["policy"] == "preserved"

    explicit_plan = plan_git_update(
        str(release),
        "v0.1.1",
        project,
        runtimes=("codex",),
        include_foundation=True,
    )
    explicit_foundation = next(
        item for item in explicit_plan["changes"] if item["component"] == "foundation"
    )
    assert explicit_foundation["from"] == "0.1.0"
    assert explicit_foundation["to"] == "0.1.1"
    assert explicit_foundation["from_digest"] == current["foundation"]["digest"]
    assert explicit_foundation["to_digest"] == target_manifest["foundation"]["digest"]
    assert explicit_foundation["changed"] is True
    assert explicit_foundation["policy"] == "explicit"


def test_rollback_staging_failure_preserves_current_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from isekai import distribution as distribution_module

    project = _project_with_foundation(tmp_path)
    _install(project)
    release = _copy_release(tmp_path)
    _bump_release(release, "0.1.1")
    install_from_checkout(
        release,
        project,
        source="https://example.invalid/isekai.git",
        ref="v0.1.1",
        commit="b" * 40,
        runtimes=("all",),
        update=True,
    )
    project_before = (project / "project.json").read_bytes()
    lock_before = (project / "isekai.lock.json").read_bytes()
    core_before = tree_digest(project / ".isekai/runtime/isekai")
    kiro_before = tree_digest(project / ".kiro/skills/isekai")

    monkeypatch.setattr(
        distribution_module,
        "_copy_managed_root",
        lambda _source, _destination: (_ for _ in ()).throw(
            OSError("forced rollback staging failure")
        ),
    )

    with pytest.raises(OSError, match="forced rollback staging failure"):
        rollback_install(project)

    assert (project / "project.json").read_bytes() == project_before
    assert (project / "isekai.lock.json").read_bytes() == lock_before
    assert tree_digest(project / ".isekai/runtime/isekai") == core_before
    assert tree_digest(project / ".kiro/skills/isekai") == kiro_before
    assert doctor_install(project)["ready"] is True


def test_rollback_restores_absent_project_manifest(tmp_path: Path) -> None:
    project = tmp_path / "product"
    project.mkdir()
    common = {
        "source": "https://example.invalid/isekai.git",
        "ref": "v0.1.0",
        "commit": "a" * 40,
    }
    install_from_checkout(ROOT, project, runtimes=("codex",), **common)
    install_from_checkout(ROOT, project, runtimes=("kiro",), **common)
    assert not (project / "project.json").exists()
    (project / "project.json").write_text(
        json.dumps(
            {
                "id": "product",
                "kind": "project",
                "schema_version": "1.0.0",
                "version": "0.1.0",
                "foundation_path": ".isekai/foundations/0.1.0",
                "profiles": ["software-delivery-profile"],
                "extensions": [],
                "maximum_agent_level": "L0",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert doctor_install(project)["ready"] is True

    rollback_install(project)
    restored = load_install_lock(project)

    assert restored is not None
    assert set(restored["adapters"]) == {"codex"}
    assert not (project / "project.json").exists()
    assert not (project / ".kiro/skills/isekai").exists()
    assert doctor_install(project)["ready"] is True


def test_managed_kiro_symlink_is_rejected_before_update(tmp_path: Path) -> None:
    project = _project_with_foundation(tmp_path)
    _install(project)
    lock_before = (project / "isekai.lock.json").read_bytes()
    kiro = project / ".kiro/skills/isekai"
    kiro_copy = project / ".kiro/skills/isekai-copy"
    shutil.copytree(kiro, kiro_copy)
    shutil.rmtree(kiro)
    kiro.symlink_to(kiro_copy, target_is_directory=True)

    health = doctor_install(project)

    assert health["ready"] is False
    assert any(
        "adapter:kiro.path contains a symlink" in issue for issue in health["issues"]
    )
    with pytest.raises(
        DistributionError, match="adapter:kiro.path contains a symlink"
    ):
        install_from_checkout(
            ROOT,
            project,
            source="https://example.invalid/isekai.git",
            ref="v0.1.1",
            commit="b" * 40,
            runtimes=("kiro",),
            update=True,
        )
    assert (project / "isekai.lock.json").read_bytes() == lock_before
    assert kiro.is_symlink()
    assert not list(project.glob(".isekai-kiro-backup-*"))


def test_rollback_postflight_failure_restores_current_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from isekai import distribution as distribution_module

    project = _project_with_foundation(tmp_path)
    _install(project)
    release = _copy_release(tmp_path)
    _bump_release(release, "0.1.1")
    install_from_checkout(
        release,
        project,
        source="https://example.invalid/isekai.git",
        ref="v0.1.1",
        commit="b" * 40,
        runtimes=("all",),
        update=True,
    )
    project_before = (project / "project.json").read_bytes()
    lock_before = (project / "isekai.lock.json").read_bytes()
    core_before = tree_digest(project / ".isekai/runtime/isekai")
    kiro_before = tree_digest(project / ".kiro/skills/isekai")
    real_doctor = distribution_module.doctor_install
    calls = 0

    def fail_postflight(target: Path) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_doctor(target)
        return {"ready": False, "issues": ["forced rollback postflight failure"]}

    monkeypatch.setattr(distribution_module, "doctor_install", fail_postflight)

    with pytest.raises(DistributionError, match="forced rollback postflight failure"):
        rollback_install(project)

    assert (project / "project.json").read_bytes() == project_before
    assert (project / "isekai.lock.json").read_bytes() == lock_before
    assert tree_digest(project / ".isekai/runtime/isekai") == core_before
    assert tree_digest(project / ".kiro/skills/isekai") == kiro_before
    assert real_doctor(project)["ready"] is True


@pytest.mark.parametrize("symlink_parent", [".kiro", ".kiro/skills"])
def test_new_kiro_install_rejects_symlinked_parent_paths(
    tmp_path: Path, symlink_parent: str
) -> None:
    project = _project_with_foundation(tmp_path)
    external = tmp_path / "external-kiro-root"
    external.mkdir()
    parent = project / symlink_parent
    parent.parent.mkdir(parents=True, exist_ok=True)
    parent.symlink_to(external, target_is_directory=True)

    with pytest.raises(DistributionError, match="adapter:kiro.path contains a symlink"):
        install_from_checkout(
            ROOT,
            project,
            source="https://example.invalid/isekai.git",
            ref="v0.1.0",
            commit="a" * 40,
            runtimes=("kiro",),
        )

    assert not list(external.rglob("SKILL.md"))
    assert not (project / ".isekai").exists()
    assert not (project / "isekai.lock.json").exists()
