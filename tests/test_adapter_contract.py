from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ACTIONS = {
    "init",
    "on",
    "off",
    "status",
    "intake",
    "route",
    "inception",
    "compatibility",
    "release-check",
    "foundation-decision",
    "foundation-evidence",
    "foundation-promote",
    "resume",
    "unit-init",
    "checkpoint",
    "envelope-propose",
    "authorize",
    "evidence",
    "decision",
    "transition",
    "verify",
}
EXPECTED_WRITES = {
    "init",
    "unit-init",
    "checkpoint",
    "envelope-propose",
    "evidence",
    "decision",
    "transition",
    "foundation-decision",
    "foundation-evidence",
    "foundation-promote",
}


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_plugin_manifest_actions_and_write_boundary_are_consistent() -> None:
    manifest = read_json(ROOT / "plugin/isekai/manifest.json")

    assert manifest["core"]["package"] == "isekai-agent-plugin"
    assert manifest["core"]["user_interface"] == "isekai <action>"
    assert set(manifest["actions"]) == EXPECTED_ACTIONS
    assert set(manifest["writes"]) == EXPECTED_WRITES
    assert set(manifest["writes"]) <= set(manifest["actions"])
    assert manifest["high_risk_actions"] == []


def test_all_runtime_adapter_surfaces_exist_and_parse() -> None:
    manifest = read_json(ROOT / "plugin/isekai/manifest.json")
    runtimes = {runtime["id"]: runtime for runtime in manifest["runtimes"]}
    assert set(runtimes) == {"kiro", "claude", "codex"}

    assert (ROOT / runtimes["kiro"]["path"]).is_file()
    assert read_json(ROOT / "plugin/isekai/runtimes/claude/.claude-plugin/plugin.json")
    assert read_json(ROOT / "plugin/isekai/runtimes/codex/.codex-plugin/plugin.json")


def test_runtime_skill_documents_expose_the_same_action_contract() -> None:
    skill_paths = [
        ROOT / ".kiro/skills/isekai/SKILL.md",
        ROOT / "plugin/isekai/runtimes/claude/skills/isekai/SKILL.md",
        ROOT / "plugin/isekai/runtimes/codex/skills/isekai/SKILL.md",
    ]
    for path in skill_paths:
        content = path.read_text(encoding="utf-8")
        for action in EXPECTED_ACTIONS:
            assert any(
                line.strip() == action or line.strip().startswith(action + " ")
                for line in content.splitlines()
            ), f"{path} does not document {action}"


def test_adapter_readmes_preserve_core_boundary_and_no_high_risk_actions() -> None:
    for path in [
        ROOT / "plugin/isekai/runtimes/kiro/README.md",
        ROOT / "plugin/isekai/runtimes/claude/README.md",
        ROOT / "plugin/isekai/runtimes/codex/README.md",
    ]:
        content = path.read_text(encoding="utf-8")
        assert "ISEKAI Core" in content
        assert "high-risk" in content.lower()
        assert "off by default" in content
        assert "`on`" in content or "$isekai on" in content
        assert "`off`" in content or "$isekai off" in content
        assert "without writing artifacts or checkpoints" in content
        assert "init --path PATH" in content
        assert "multiple candidates require user selection" in content.lower()
        assert "units/**/evidence/raw/" in content


def test_runtime_skills_share_conversation_mode_contract() -> None:
    skill_paths = [
        ROOT / ".kiro/skills/isekai/SKILL.md",
        ROOT / "plugin/isekai/runtimes/claude/skills/isekai/SKILL.md",
        ROOT / "plugin/isekai/runtimes/codex/skills/isekai/SKILL.md",
    ]
    required_contract = [
        "off by default in every new conversation",
        "normalize each new request through `intake`",
        "separate from Unit lifecycle status",
        "new or interrupted session",
        "without activating persistent conversation mode",
        "descendant workspace candidates",
        "get explicit user confirmation before initializing",
        "Project root's `units/`",
        "never selects or resumes a Unit",
        "invoke `resume` separately",
    ]
    for path in skill_paths:
        content = path.read_text(encoding="utf-8")
        for phrase in required_contract:
            assert phrase in content, f"{path} is missing mode contract: {phrase}"
