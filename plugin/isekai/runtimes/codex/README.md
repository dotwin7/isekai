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

The ISEKAI Project installer keeps the complete thin Plugin under the managed Project runtime and publishes it through Codex's documented repo marketplace:

```text
.agents/plugins/marketplace.json
.isekai/marketplaces/codex/plugins/isekai-agent-plugin/
```

`isekai install --runtime codex` records the Plugin version and digest in `isekai.lock.json`. It never registers a Project path in user-global Codex configuration. The repo marketplace marks the Plugin installed by default inside a trusted Project, while `agents/openai.yaml` sets `allow_implicit_invocation: false`; only an explicit `$isekai-agent-plugin:isekai` command may invoke the Skill. Start a new conversation after first installation or an Adapter update and run the Core `on`, `resume`, and `verify` Golden Path smoke.

## Conversation mode

The Adapter is discoverable but ISEKAI mode is off by default in each new conversation. Discovery, plugin installation, a leftover cache, repository contents, and a textual mention of `$isekai-agent-plugin:isekai` are not activation and must not trigger the Skill. Only an intentional `$isekai-agent-plugin:isekai <action>` command is a one-shot invocation while mode is off. Invoke `$isekai-agent-plugin:isekai on` from a Project root to activate Project-level intake routing for later requests and list Unit candidates without selecting or resuming one. Use `$isekai-agent-plugin:isekai resume [--project PATH] [--unit PATH]` separately to restore a Unit. `$isekai-agent-plugin:isekai off` stops routing without writing artifacts or checkpoints; it does not enable, disable, install, or unload the Codex plugin.

Core searches the current directory, ancestors, and unambiguous descendant candidates. If no manifest exists, `$isekai-agent-plugin:isekai init --path PATH` creates a validated manifest and Project-local `units/` after explicit confirmation; multiple candidates require user selection. Sensitive raw Evidence belongs under ignored `units/**/evidence/raw/`.

The Skill requires the selected Project launcher `.isekai/bin/isekai`, never falls back to a global executable, performs a version/protocol handshake, and then calls the ISEKAI Plugin contract. ISEKAI Core and Unit artifacts remain authoritative. Before a governed read, edit, or test, `authorize` supplies a Project target and records a bounded grant in the Unit authorization ledger. The Adapter does not perform high-risk actions.

## Compatibility

The verified CLI baseline is `codex 0.146.0`. It is an observed test version, not a minimum requirement. After a CLI upgrade, rerun Plugin validation, Codex Skill policy/command-surface checks, and the Core status/resume/verify Golden Path smoke. Keep the repo marketplace path unless Codex changes its documented discovery contract.
