from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from isekai.runtime.actions import _compatibility_issues


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
    "project-knowledge-status",
    "project-knowledge-propose",
    "project-knowledge-promote",
    "resume",
    "unit-migrate",
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
    "unit-migrate",
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
    "project-knowledge-propose",
    "project-knowledge-promote",
}


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_packaged_and_runtime_compatibility_matrices_cannot_drift() -> None:
    packaged = read_json(ROOT / "src/isekai/data/compatibility.json")
    runtime = read_json(ROOT / "runtime/compatibility.json")

    assert packaged == runtime
    assert _compatibility_issues(packaged) == []
    manifest = read_json(ROOT / "runtime/manifest.json")
    assert packaged["trust_model"] == manifest["trust_model"]
    assert packaged["runtime_contract"] == {
        "high_risk_actions": manifest["high_risk_actions"],
        "human_decision_actions": manifest["human_decision_actions"],
    }


def test_tested_runtime_versions_require_linked_live_evidence() -> None:
    matrix = read_json(ROOT / "runtime/compatibility.json")
    broken = copy.deepcopy(matrix)
    broken["runtimes"][0]["tested_versions"] = ["99.0.0"]

    assert any("tested_versions lack live evidence" in issue for issue in _compatibility_issues(broken))


def test_live_smoke_writes_digest_bound_surface_evidence(tmp_path: Path) -> None:
    evidence_path = tmp_path / "runtime-smoke.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/live-smoke.py"),
            "--runtime",
            "codex",
            "--evidence-output",
            str(evidence_path),
            "--recorded-by",
            "adapter-contract-test",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    evidence = read_json(evidence_path)
    digest = evidence.pop("evidence_digest")
    encoded = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert digest == "sha256:" + hashlib.sha256(encoded).hexdigest()
    assert evidence["recorded_by"] == "adapter-contract-test"
    assert evidence["attestation"] == {
        "type": "runtime-smoke-attestation",
        "reported_actor": "adapter-contract-test",
        "identity_verification": "not-performed-by-script",
        "execution_verification": "script-observed-subprocess",
    }
    release_digest = hashlib.sha256(
        (ROOT / "distribution/release.json").read_bytes()
    ).hexdigest()
    assert evidence["release_manifest_digest"] == "sha256:" + release_digest
    assert evidence["observations"] == [
        {
            "runtime": "codex",
            "status": "surface-only",
            "version": None,
            "checks": [
                "installed Runtime surface exists",
                "project-local doctor reported ready",
            ],
            "surfaces": [".agents/skills/isekai/SKILL.md"],
        }
    ]


def test_runtime_manifest_actions_and_write_boundary_are_consistent() -> None:
    manifest = read_json(ROOT / "runtime/manifest.json")

    assert manifest["core"]["package"] == "isekai-ai-dlc-runtime"
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
        "foundation-decision",
        "foundation-promote",
    }
    assert human <= set(manifest["writes"])
    assert manifest["trust_model"] == {
        "core_enforcement": "record-consistency-and-tamper-detection",
        "action_execution": "runtime-host-outside-core",
        "human_identity": "caller-attested-not-core-verified",
        "evidence_execution": "runtime-attested-not-core-executed",
        "external_controls_required": [
            "runtime sandbox and permission policy",
            "authenticated human confirmation channel",
            "CI or host execution provenance",
        ],
    }


def test_all_runtime_adapter_surfaces_exist_and_parse() -> None:
    manifest = read_json(ROOT / "runtime/manifest.json")
    runtimes = {runtime["id"]: runtime for runtime in manifest["adapters"]}
    assert set(runtimes) == {"kiro", "claude", "codex"}
    assert not (ROOT / ".kiro").exists()

    assert (ROOT / runtimes["kiro"]["path"]).is_file()
    assert (ROOT / runtimes["claude"]["path"]).is_file()
    assert (ROOT / runtimes["codex"]["path"]).is_file()


def test_runtime_skill_documents_expose_the_same_action_contract() -> None:
    skill_paths = [
        ROOT / "runtime/adapters/kiro/skills/isekai/SKILL.md",
        ROOT / "runtime/adapters/claude/skills/isekai/SKILL.md",
        ROOT / "runtime/adapters/codex/skills/isekai/SKILL.md",
    ]
    for path in skill_paths:
        content = path.read_text(encoding="utf-8")
        for action in EXPECTED_ACTIONS:
            assert any(
                line.strip() == action or line.strip().startswith(action + " ")
                for line in content.splitlines()
            ), f"{path} does not document {action}"


def test_runtime_skills_match_the_canonical_template() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate-runtime-skills.py"), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_runtime_host_surface_checker_accepts_all_source_adapters() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/runtime-host-check.py"),
            "--runtime",
            "all",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["valid"] is True
    assert {entry["runtime"] for entry in result["runtimes"]} == {
        "codex",
        "claude",
        "kiro",
    }
    assert all(entry["level"] == "surface-only" for entry in result["runtimes"])


def test_adapter_readmes_preserve_core_boundary_and_no_high_risk_actions() -> None:
    for path in [
        ROOT / "runtime/adapters/kiro/README.md",
        ROOT / "runtime/adapters/claude/README.md",
        ROOT / "runtime/adapters/codex/README.md",
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
        ROOT / "runtime/adapters/kiro/skills/isekai/SKILL.md",
        ROOT / "runtime/adapters/claude/skills/isekai/SKILL.md",
        ROOT / "runtime/adapters/codex/skills/isekai/SKILL.md",
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
        "Treat one resumed Unit as the only active Unit for persistent work.",
        "Never use its Envelope to read, edit, or test a sibling Unit.",
    ]
    for path in skill_paths:
        content = path.read_text(encoding="utf-8")
        for phrase in required_contract:
            assert phrase in content, f"{path} is missing mode contract: {phrase}"


def test_runtime_skills_share_adaptive_driver_contract() -> None:
    skill_paths = [
        ROOT / "runtime/adapters/kiro/skills/isekai/SKILL.md",
        ROOT / "runtime/adapters/claude/skills/isekai/SKILL.md",
        ROOT / "runtime/adapters/codex/skills/isekai/SKILL.md",
    ]
    required_contract = [
        "The host agent drives the lifecycle",
        "returned `workflow` object as the orchestration contract",
        "For `direct-response`",
        "For `bounded-change`",
        "For `adaptive-unit`",
        "Level-1 plan",
        "every lifecycle stage with `apply` or `skip`",
        "Ask only questions whose answers would materially change the plan.",
        "one explicit user approval for the complete Level-1 plan",
        "Do not ask again for every file, checkpoint, `envelope-approve`, or `transition`.",
        "human_decision_actions",
        "Read `document_language` from the selected Project and Unit",
        "Decision descriptions in Korean",
        "Never replace a Project's language merely to simplify generation",
        "human-facing `title` from `unit_candidate_details`",
        "classify the full user request and conversation context",
        "Core performs conservative text inference as defense in depth",
        "[--ambiguous] [--multi-party] [--remote] [--sensitive]",
        "## Human confirmation boundary",
        "Read `status` or `resume` field `human_gate`",
        "Human Gates are repeatable review loops, not one-shot acknowledgements.",
        "corrections, additional requirements",
        "Never reuse an earlier approval for the revised result.",
        "reject → revise → verify → re-request",
        "Do not silently finish after implementing review feedback.",
        "An unattended, headless, `dontAsk`, bypass-permission, or pre-trusted tool session cannot originate a new human Decision.",
    ]
    for path in skill_paths:
        content = path.read_text(encoding="utf-8")
        for phrase in required_contract:
            assert phrase in content, f"{path} is missing driver contract: {phrase}"


def test_runtime_skills_require_explicit_invocation_before_activation() -> None:
    skill_commands = {
        ROOT / "runtime/adapters/kiro/skills/isekai/SKILL.md": (
            "/isekai ACTION",
            "/isekai on",
        ),
        ROOT / "runtime/adapters/claude/skills/isekai/SKILL.md": (
            "/isekai ACTION",
            "/isekai on",
        ),
        ROOT / "runtime/adapters/codex/skills/isekai/SKILL.md": (
            "$isekai ACTION",
            "$isekai on",
        ),
    }
    shared_gate = [
        "Explicit-command-only",
        "discovery is not activation",
        "documentation, code, logs, or review feedback is not an invocation.",
        "never activate ISEKAI",
        "While mode is off and no intentional command was invoked",
        "do not run a launcher, `handshake`, Core, `intake`, `route`, `inception`, `status`, or `resume`",
        "activates automatic ISEKAI routing for later ordinary requests",
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

    codex = (
        ROOT / "runtime/adapters/codex/skills/isekai/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "Project-local Runtime Skill as `$isekai ACTION`" in codex
    assert "`$isekai on [--project PATH]`" in codex

    claude = (
        ROOT / "runtime/adapters/claude/skills/isekai/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "Project-local Runtime Skill as `/isekai ACTION`" in claude
    assert "`/isekai on [--project PATH]`" in claude

    kiro = (
        ROOT / "runtime/adapters/kiro/skills/isekai/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "ISEKAI_HEADLESS: ACTION" in kiro.split("---", maxsplit=2)[1]
    assert "--trust-all-tools` as a substitute for a human gate" in kiro


def test_runtime_skills_are_project_local_and_never_use_a_global_launcher() -> None:
    skill_paths = {
        "kiro": ROOT / "runtime/adapters/kiro/skills/isekai/SKILL.md",
        "claude": ROOT / "runtime/adapters/claude/skills/isekai/SKILL.md",
        "codex": ROOT / "runtime/adapters/codex/skills/isekai/SKILL.md",
    }
    for runtime, path in skill_paths.items():
        content = path.read_text(encoding="utf-8")
        assert "Never fall back to an `isekai` command from `PATH`." in content
        assert "<PROJECT_ROOT>/.isekai/bin/isekai runtime <action>" in content
        assert f"handshake --runtime {runtime}" in content

    claude = skill_paths["claude"].read_text(encoding="utf-8")
    assert "disable-model-invocation: true" in claude.split("---", maxsplit=2)[1]

    codex_policy = (
        ROOT
        / "runtime/adapters/codex/skills/isekai/agents/openai.yaml"
    ).read_text(encoding="utf-8")
    assert "allow_implicit_invocation: false" in codex_policy
