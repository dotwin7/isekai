from __future__ import annotations

import copy
import hashlib
import json
import os
import runpy
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
    "catalog-status",
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
    "amend",
    "active-unit-detach",
    "envelope-propose",
    "envelope-approve",
    "authorize",
    "managed-edit",
    "prove",
    "artifact-write",
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
    "amend",
    "active-unit-detach",
    "envelope-propose",
    "envelope-approve",
    "authorize",
    "managed-edit",
    "prove",
    "artifact-write",
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
    assert packaged["catalog_model"] == manifest["catalog_model"]
    assert packaged["runtime_contract"] == {
        "high_risk_actions": manifest["high_risk_actions"],
        "human_decision_actions": manifest["human_decision_actions"],
        "external_agent_actions": manifest["external_agent_actions"],
        "credential_handling": manifest["credential_handling"],
    }


def test_tested_runtime_versions_require_linked_live_evidence() -> None:
    matrix = read_json(ROOT / "runtime/compatibility.json")
    broken = copy.deepcopy(matrix)
    broken["runtimes"][0]["tested_versions"] = ["99.0.0"]

    assert any("tested_versions lack live evidence" in issue for issue in _compatibility_issues(broken))


def test_catalog_permission_contract_cannot_be_silently_weakened() -> None:
    matrix = read_json(ROOT / "runtime/compatibility.json")
    broken = copy.deepcopy(matrix)
    broken["catalog_model"]["permission_effect"] = "may-expand-unit-authority"

    assert "compatibility matrix has an invalid catalog_model" in (
        _compatibility_issues(broken)
    )


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


def test_live_smoke_automates_all_host_sessions_and_golden_path(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_host = """#!__PYTHON__
import json
import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
prompt = args[-1] if args else ""
is_off_prompt = any(
    marker in prompt
    for marker in ("$isekai off", "/isekai off", "ISEKAI_HEADLESS: off")
)
if "--version" in args:
    versions = {
        "codex": "codex-cli 0.147.0",
        "claude": "claude 2.1.224",
        "kiro-cli": "kiro-cli 2.16.2",
    }
    print(versions[name])
    raise SystemExit(0)
if "--help" in args:
    capabilities = {
        "codex": (
            "SESSION_ID --ephemeral --ignore-user-config --json "
            "--skip-git-repo-check"
            if "resume" in args
            else "--ephemeral --ignore-user-config --json --sandbox "
            "--skip-git-repo-check"
        ),
        "claude": (
            "--no-session-persistence --output-format --permission-mode --print "
            "--resume --tools --verbose"
        ),
        "kiro-cli": (
            "--agent --no-interactive --require-mcp-startup --resume --trust-tools"
        ),
    }
    print(capabilities[name])
    raise SystemExit(0)
if name == "codex" and "resume" in args and "--sandbox" in args:
    print("exec resume does not accept --sandbox", file=sys.stderr)
    raise SystemExit(2)
if ("resume" in args or "--resume" in args) and any(
    token in prompt.lower() for token in ("isekai", "intake", "runtime_action")
) and not is_off_prompt:
    print("follow-up prompt leaked the expected orchestration", file=sys.stderr)
    raise SystemExit(2)

def write_kiro_trace(items):
    target = os.environ.get("KIRO_ACP_RECORD_PATH")
    if not target:
        print("missing KIRO_ACP_RECORD_PATH", file=sys.stderr)
        raise SystemExit(2)
    Path(target).write_text(
        "".join(json.dumps(item) + "\\n" for item in items),
        encoding="utf-8",
    )

def kiro_items(action, result):
    call_id = "call-" + action
    return [
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"update": {
                "sessionUpdate": "tool_call",
                "toolCallId": call_id,
                "title": "isekai_core___runtime_action",
                "status": "pending",
                "rawInput": {"action": action, "payload": {}},
            }},
        },
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": call_id,
                "status": "completed",
                "rawOutput": {"action": action, "result": result},
            }},
        },
    ]

if name == "codex":
    def item(command, action, result):
        print(json.dumps({
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "name": "runtime_action",
                "arguments": {"action": action, "payload": {}},
                "result": json.dumps({"action": action, "result": result}),
            },
        }))

    print(json.dumps({"type": "thread.started", "thread_id": "fake-thread"}))
    if is_off_prompt:
        item(".isekai/bin/isekai runtime handshake --runtime codex", "handshake", {
            "compatible": True
        })
        item(".isekai/bin/isekai runtime off", "off", {
            "adapter_mode": {"state": "off"}
        })
    elif "resume" in args:
        item(".isekai/bin/isekai runtime intake --project /tmp/project", "intake", {
            "route": {"route": "query"}, "workflow": {"directive": "direct-response"}
        })
    elif "status, resume, and verify" in prompt:
        item(".isekai/bin/isekai runtime status --project /tmp/golden", "status", {
            "unit": {"status": "learned"}
        })
        item(".isekai/bin/isekai runtime resume --project /tmp/golden", "resume", {
            "unit": {"status": "learned"}
        })
        item(".isekai/bin/isekai runtime verify --unit /tmp/unit", "verify", {
            "valid": True
        })
    else:
        print(json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "injected Skill activated"},
        }))
        item(".isekai/bin/isekai runtime handshake --runtime codex", "handshake", {
            "compatible": True
        })
        item(".isekai/bin/isekai runtime on --project /tmp/project", "on", {
            "adapter_mode": {"state": "on"}
        })
elif name == "claude":
    print(json.dumps({"type": "system", "session_id": "fake-claude-session"}))
    def claude_item(command, action, result):
        print(json.dumps({
                "type": "assistant",
                "session_id": "fake-claude-session",
                "message": {"content": [{
                    "type": "tool_use",
                    "name": "mcp__isekai_core__runtime_action",
                    "input": {"action": action, "payload": {}}
            }]},
        }))
        print(json.dumps({
            "type": "user",
            "session_id": "fake-claude-session",
            "message": {"content": [{
                "type": "tool_result",
                "content": json.dumps({"action": action, "result": result}),
            }]},
        }))
    if is_off_prompt:
        claude_item(".isekai/bin/isekai runtime handshake --runtime claude", "handshake", {
            "compatible": True
        })
        claude_item(".isekai/bin/isekai runtime off", "off", {
            "adapter_mode": {"state": "off"}
        })
    elif "--resume" in args:
        claude_item(".isekai/bin/isekai runtime intake --project /tmp/project", "intake", {
            "route": {"route": "query"}, "workflow": {"directive": "direct-response"}
        })
    elif "status, resume, and verify" in prompt:
        claude_item(".isekai/bin/isekai runtime status --project /tmp/golden", "status", {
            "unit": {"status": "learned"}
        })
        claude_item(".isekai/bin/isekai runtime resume --project /tmp/golden", "resume", {
            "unit": {"status": "learned"}
        })
        claude_item(".isekai/bin/isekai runtime verify --unit /tmp/unit", "verify", {
            "valid": True
        })
    else:
        claude_item(".isekai/bin/isekai runtime handshake --runtime claude", "handshake", {
            "compatible": True
        })
        claude_item(".isekai/bin/isekai runtime on --project /tmp/project", "on", {
            "adapter_mode": {"state": "on"}
        })
else:
    if is_off_prompt:
        write_kiro_trace([
            *kiro_items("handshake", {"compatible": True}),
            *kiro_items("off", {"adapter_mode": {"state": "off"}}),
        ])
    elif "--resume" in args:
        write_kiro_trace(kiro_items("intake", {
            "route": {"route": "query"},
            "workflow": {"directive": "direct-response"},
        }))
    elif "status, resume, and verify" in prompt:
        write_kiro_trace([
            *kiro_items("status", {"unit": {"status": "learned"}}),
            *kiro_items("resume", {"unit": {"status": "learned"}}),
            *kiro_items("verify", {"valid": True}),
        ])
    else:
        write_kiro_trace([
            *kiro_items("handshake", {"compatible": True}),
            *kiro_items("on", {"adapter_mode": {"state": "on"}}),
        ])
    print("ordinary model response without embedded tool evidence")
""".replace("__PYTHON__", sys.executable)
    for executable in ("codex", "claude", "kiro-cli"):
        path = fake_bin / executable
        path.write_text(fake_host, encoding="utf-8")
        path.chmod(0o755)
    environment = dict(os.environ)
    environment["PATH"] = str(fake_bin) + os.pathsep + environment.get("PATH", "")

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/live-smoke.py"),
            "--runtime",
            "all",
            "--host",
            "all",
            "--timeout",
            "30",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["passed"] is True
    assert result["live_hosts_requested"] == ["codex", "claude", "kiro"]
    assert set(result["live_hosts"]) == {"codex", "claude", "kiro"}
    assert all(
        "same-session automatic intake" in host["checks"]
        and "explicit off invocation resolved from host arguments" in host["checks"]
        and "Golden Unit status/resume/verify valid" in host["checks"]
        for host in result["live_hosts"].values()
    )
    assert {
        runtime: {
            stage: evidence["format"]
            for stage, evidence in host["execution_evidence"].items()
        }
        for runtime, host in result["live_hosts"].items()
    } == {
        "codex": {
            "activation": "codex-jsonl",
            "followup": "codex-jsonl",
            "off": "codex-jsonl",
            "golden": "codex-jsonl",
        },
        "claude": {
            "activation": "claude-stream-json",
            "followup": "claude-stream-json",
            "off": "claude-stream-json",
            "golden": "claude-stream-json",
        },
        "kiro": {
            "activation": "kiro-acp-jsonl",
            "followup": "kiro-acp-jsonl",
            "off": "kiro-acp-jsonl",
            "golden": "kiro-acp-jsonl",
        },
    }
    assert result["golden_setup"]["doctor"]["ready"] is True
    assert result["golden_setup"]["verify"]["result"]["valid"] is True


def test_kiro_live_evidence_rejects_model_text_without_an_acp_tool_call() -> None:
    namespace = runpy.run_path(str(ROOT / "scripts/live-smoke.py"))
    parse = namespace["_kiro_acp_runtime_evidence"]
    fabricated = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "name": "isekai_core___runtime_action",
                                "action": "verify",
                                "result": {"valid": True},
                            }
                        ),
                    },
                }
            },
        }
    )

    evidence = parse(fabricated)

    assert evidence["mcp_actions"] == []
    assert evidence["action_results"] == {}
    assert evidence["completed_calls"] == 0


def test_runtime_manifest_actions_and_write_boundary_are_consistent() -> None:
    manifest = read_json(ROOT / "runtime/manifest.json")

    assert manifest["core"]["package"] == "isekai-core-runtime"
    assert manifest["core"]["user_interface"] == "isekai <action>"
    assert set(manifest["actions"]) == EXPECTED_ACTIONS
    assert set(manifest["writes"]) == EXPECTED_WRITES
    assert set(manifest["writes"]) <= set(manifest["actions"])
    assert manifest["high_risk_actions"] == []
    # Core records these as human judgments but cannot verify a human made
    # them, so adapters must obtain real user confirmation before invoking.
    human = set(manifest["human_decision_actions"])
    assert human == {
        "amend",
        "active-unit-detach",
        "decision",
        "foundation-decision",
        "foundation-promote",
    }
    assert human <= set(manifest["writes"])
    assert manifest["trust_model"] == {
        "core_enforcement": "record-consistency-tamper-detection-active-unit-binding-and-managed-execution",
        "action_execution": "core-managed-edit-and-proof",
        "proof_isolation": "os-enforced-source-and-user-data-read-denial-write-confinement-network-denial-fail-closed",
        "conversation_change_reporting": "runtime-adapter-attested-not-core-observed",
        "human_identity": "caller-attested-not-core-verified",
        "evidence_execution": "core-receipted-for-proofs",
        "secret_resolution": "runtime-host-outside-core",
        "external_controls_required": [
            "host direct-write tools disabled in favor of the Core gateway",
            "active-Unit user-turn routing by the Runtime Adapter",
            "authenticated human confirmation channel",
            "CI or host execution provenance",
            "host secret broker and output redaction",
        ],
    }
    assert manifest["catalog_model"] == {
        "unit": "versioned-isekai-catalog-entry",
        "distribution": "core-bundled-or-catalog-package",
        "exposure": "project-local-core-mcp-control-plane",
        "context_binding": "sha256-catalog-and-package-digests",
        "project_ownership": "not-a-product-extension",
        "permission_effect": "cannot-expand-foundation-project-or-unit-authority",
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
        "Treat every new user message as part of the bound Unit",
        "Core blocks a new `intake`",
        "final transition to `learned` clears it mechanically",
        ".isekai-runtime/active-unit.json",
        "without activating persistent conversation mode",
        "descendant workspace candidates",
        "get explicit user confirmation before initializing",
        "Project root's `units/`",
        "never selects or resumes a Unit",
        "Use `on` after a new session",
        "The Project has only one Core-bound active Unit for persistent work.",
        "Never use its Envelope to read, edit, or test a sibling Unit.",
        "invoke lifecycle actions through the connected `isekai-core` MCP `runtime_action` tool",
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
        "autonomy-bounded plan",
        "every lifecycle stage with `apply` or `skip`",
        "Ask only questions whose answers would materially change the plan.",
        "one explicit user approval for the complete autonomy-bounded plan",
        "Immediately after `unit-init` and before the first lifecycle transition",
        "Never leave an approved plan only in the conversation",
        "Complete the approved source implementation during Construction",
        "do not defer the primary implementation until after the Validation transition",
        "Treat `checkpoint_required: true`",
        "checkpoint before any progress report",
        "On `resume`, inspect `checkpoint_fresh`",
        "Never present a stale `next_action` as authoritative.",
        "Project-scoped active Unit binding",
        "invoke `amend` before implementation",
        "not a rejection",
        "Core rejects another route or Unit.",
        "active-unit-amendment-required",
        "`active-unit-detach` is a human-decision action",
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
        "Record additions or requested changes with `amend`",
        "Never reuse an earlier approval for a revised result",
        "fresh packet with the amendment ID",
        "silently finish after implementing feedback",
        "An unattended, headless, `dontAsk`, bypass-permission, or pre-trusted tool session cannot originate a new human Decision.",
        "exclusive Core execution boundary, not lifecycle hooks",
        "Host `Edit`, `Write`, `apply_patch`",
        "use Core `artifact-write`",
        "Use Core `managed-edit`",
        "Runtime `authorize --action edit|test` is intentionally denied.",
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


def test_runtime_skills_accept_host_bound_off_without_requiring_prefix_again() -> None:
    skills = {
        "codex": ROOT / "runtime/adapters/codex/skills/isekai/SKILL.md",
        "claude": ROOT / "runtime/adapters/claude/skills/isekai/SKILL.md",
        "kiro": ROOT / "runtime/adapters/kiro/skills/isekai/SKILL.md",
    }
    shared_contract = (
        "host-provided invocation arguments",
        "literal command prefix",
        "ask the user to invoke the same command again",
        "If the resolved action is exactly `off`",
        "call the connected Core `runtime_action` with action `off`",
        "Do not ask for confirmation, inspect or resume a Unit, write a Checkpoint, or run intake first.",
        "quoted or pasted Skill content",
    )

    for runtime, path in skills.items():
        content = path.read_text(encoding="utf-8")
        for phrase in shared_contract:
            assert phrase in content, f"{runtime} is missing invocation transport: {phrase}"

    codex = skills["codex"].read_text(encoding="utf-8")
    assert "loaded as that active named Skill" in codex
    assert "model-visible text is only `off`" in codex

    claude = skills["claude"].read_text(encoding="utf-8")
    assert "user-selected Skill with a `/isekai` command envelope" in claude
    assert "$ARGUMENTS" in claude

    kiro = skills["kiro"].read_text(encoding="utf-8")
    assert "explicitly selected `/isekai` slash command" in kiro
    assert "remaining command text" in kiro


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
