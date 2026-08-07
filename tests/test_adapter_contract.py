from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ACTIONS = {
    "init",
    "handshake",
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
    "envelope-approve",
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
    "envelope-approve",
    "authorize",
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
    # Core records these as human judgments but cannot verify a human made
    # them, so adapters must obtain real user confirmation before invoking.
    human = set(manifest["human_decision_actions"])
    assert human == {
        "decision",
        "envelope-approve",
        "transition",
        "foundation-decision",
        "foundation-promote",
    }
    assert human <= set(manifest["writes"])


def test_all_runtime_adapter_surfaces_exist_and_parse() -> None:
    manifest = read_json(ROOT / "plugin/isekai/manifest.json")
    runtimes = {runtime["id"]: runtime for runtime in manifest["runtimes"]}
    assert set(runtimes) == {"kiro", "claude", "codex"}
    assert not (ROOT / ".kiro").exists()

    assert (ROOT / runtimes["kiro"]["path"]).is_file()
    assert read_json(ROOT / "plugin/isekai/runtimes/claude/.claude-plugin/plugin.json")
    assert read_json(ROOT / "plugin/isekai/runtimes/codex/.codex-plugin/plugin.json")


def test_runtime_skill_documents_expose_the_same_action_contract() -> None:
    skill_paths = [
        ROOT / "plugin/isekai/runtimes/kiro/skills/isekai/SKILL.md",
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
        assert "not activation" in content
        assert "leftover cache" in content
        assert "textual mention" in content
        assert "must not trigger the Skill" in content
        assert "`on`" in content or "isekai on" in content
        assert "`off`" in content or "isekai off" in content
        assert "without writing artifacts or checkpoints" in content
        assert "init --path PATH" in content
        assert "multiple candidates require user selection" in content.lower()
        assert "units/**/evidence/raw/" in content


def test_runtime_skills_share_conversation_mode_contract() -> None:
    skill_paths = [
        ROOT / "plugin/isekai/runtimes/kiro/skills/isekai/SKILL.md",
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


def test_runtime_skills_require_explicit_invocation_before_activation() -> None:
    skill_commands = {
        ROOT / "plugin/isekai/runtimes/kiro/skills/isekai/SKILL.md": (
            "/isekai ACTION",
            "/isekai on",
        ),
        ROOT / "plugin/isekai/runtimes/claude/skills/isekai/SKILL.md": (
            "/isekai-agent-plugin:isekai ACTION",
            "/isekai-agent-plugin:isekai on",
        ),
        ROOT / "plugin/isekai/runtimes/codex/skills/isekai/SKILL.md": (
            "$isekai-agent-plugin:isekai ACTION",
            "$isekai-agent-plugin:isekai on",
        ),
    }
    shared_gate = [
        "Explicit-command-only ISEKAI adapter.",
        "discovery is not activation",
        "A command shown or discussed in prose, documentation, code, logs, or review feedback is not an invocation.",
        "never activate ISEKAI",
        "While mode is off and no intentional command was invoked",
        "do not run a launcher, `handshake`, Core, `intake`, `route`, `inception`, `status`, or `resume`",
        "activates automatic ISEKAI routing for later ordinary requests in the current conversation",
        "All other explicit actions are one-shot and leave mode off.",
        "If activation state is not explicit in the current conversation, treat it as off.",
    ]

    for path, (command, on_command) in skill_commands.items():
        content = path.read_text(encoding="utf-8")
        frontmatter = content.split("---", maxsplit=2)[1]
        assert command in frontmatter
        assert on_command in frontmatter
        assert "ordinary project work" in frontmatter
        assert "discovery" in frontmatter.lower()
        for phrase in shared_gate:
            assert phrase in content, f"{path} is missing activation gate: {phrase}"


def test_runtime_skills_are_project_local_and_never_use_a_global_launcher() -> None:
    skill_paths = {
        "kiro": ROOT / "plugin/isekai/runtimes/kiro/skills/isekai/SKILL.md",
        "claude": ROOT / "plugin/isekai/runtimes/claude/skills/isekai/SKILL.md",
        "codex": ROOT / "plugin/isekai/runtimes/codex/skills/isekai/SKILL.md",
    }
    for runtime, path in skill_paths.items():
        content = path.read_text(encoding="utf-8")
        assert "Never fall back to an `isekai` command from `PATH`." in content
        assert "<PROJECT_ROOT>/.isekai/bin/isekai plugin <action>" in content
        assert f"handshake --runtime {runtime}" in content

    claude = skill_paths["claude"].read_text(encoding="utf-8")
    assert "disable-model-invocation: true" in claude.split("---", maxsplit=2)[1]

    codex_policy = (
        ROOT
        / "plugin/isekai/runtimes/codex/skills/isekai/agents/openai.yaml"
    ).read_text(encoding="utf-8")
    assert "allow_implicit_invocation: false" in codex_policy
