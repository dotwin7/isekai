# ISEKAI Codex adapter

This adapter follows Codex's documented plugin layout:

```text
plugin/isekai/runtimes/codex/
├── .codex-plugin/plugin.json
└── skills/isekai/
    ├── SKILL.md
    └── agents/openai.yaml
```

Validate the checked-in plugin structure with the repository contract test:

```bash
python3 -m pytest -q tests/test_adapter_contract.py
```

The ISEKAI Project installer keeps the complete thin Plugin under the managed Project runtime and copies its Skill to Codex's documented repo-local Skill location:

```text
.agents/skills/isekai/
.agents/plugins/marketplace.json
.isekai/marketplaces/codex/plugins/isekai-agent-plugin/
```

`isekai install --runtime codex` records both the Plugin package digest and repo Skill digest in `isekai.lock.json`. It never registers a Project path in user-global Codex configuration. Codex loads `.agents/skills/isekai` directly; the repo marketplace remains a separate Plugin distribution surface and is not treated as host-installed merely because its file exists. `agents/openai.yaml` sets `allow_implicit_invocation: false`, so invoke the repo Skill explicitly as `$isekai`. If the Plugin is separately installed from a configured marketplace, `$isekai-agent-plugin:isekai` is the namespaced alias. Start a new conversation after installation or an Adapter update.

## Conversation mode

The Adapter is discoverable but ISEKAI mode is off by default in each new conversation. Discovery, Plugin or Skill installation, a leftover cache, and repository contents are not activation. A textual mention of a command is not activation either and must not trigger the Skill. Invoke `$isekai on` from a Project root to activate Project-level intake routing for later requests and list Unit candidates without selecting or resuming one. Use `$isekai resume [--project PATH] [--unit PATH]` separately to restore a Unit. `$isekai off` stops routing without writing artifacts or checkpoints. The namespaced Plugin alias follows the same rules.

Core searches the current directory, ancestors, and unambiguous descendant candidates. If no manifest exists, `$isekai init --path PATH` creates a validated manifest and Project-local `units/` after explicit confirmation; multiple candidates require user selection. Sensitive raw Evidence belongs under ignored `units/**/evidence/raw/`.

The Skill requires the selected Project launcher `.isekai/bin/isekai`, never falls back to a global executable, performs a version/protocol handshake, and then calls the ISEKAI Plugin contract. ISEKAI Core and Unit artifacts remain authoritative. Before a governed read, edit, or test, `authorize` supplies a Project target and records a bounded grant in the Unit authorization ledger. The Adapter does not perform high-risk actions.

## Compatibility

The verified CLI baselines are `codex 0.146.0` and `0.147.0`. They are observed test versions, not minimum requirements. Version `0.147.0` passed injected repo Skill activation, two-turn automatic intake, and the Core status/resume/verify Golden Path smoke. After a CLI upgrade, rerun `scripts/live-smoke.py --runtime codex --host codex` plus the completed-Unit Golden Path. Keep `.agents/skills/isekai` unless Codex changes its documented repo Skill discovery contract.
