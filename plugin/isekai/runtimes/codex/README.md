# ISEKAI Codex adapter

This adapter follows Codex's documented plugin layout:

```text
plugin/isekai/runtimes/codex/
├── .codex-plugin/plugin.json
└── skills/isekai/SKILL.md
```

Validate the checked-in plugin structure with the repository contract test:

```bash
python3 -m pytest -q tests/test_adapter_contract.py
```

When a repo-local Codex marketplace is configured, also install the plugin in a clean session and run the Core `on`, `resume`, and `verify` Golden Path smoke.

`isekai install --runtime codex` prepares a project-local marketplace under `.isekai/marketplaces/codex/` and records its version and digest in `isekai.lock.json`. Add `--register` only when the installer is authorized to run Codex's native marketplace add and plugin reinstall commands. Updates use a single `+codex.<commit>` cachebuster and require a new conversation. This project does not silently modify user-global Codex configuration.

## Conversation mode

The Adapter is discoverable but ISEKAI mode is off by default in each new conversation. Invoke `$isekai on` from a Project root to activate Project-level intake routing and list Unit candidates without selecting or resuming one. Use `$isekai resume [--project PATH] [--unit PATH]` separately to restore a Unit. `$isekai off` stops routing without writing artifacts or checkpoints; it does not enable, disable, install, or unload the Codex plugin.

Core searches the current directory, ancestors, and unambiguous descendant candidates. If no manifest exists, `$isekai init --path PATH` creates a validated manifest and Project-local `units/` after explicit confirmation; multiple candidates require user selection. Sensitive raw Evidence belongs under ignored `units/**/evidence/raw/`.

The Skill prefers the project launcher `.isekai/bin/isekai`, performs a version/protocol handshake, and then calls the ISEKAI Plugin contract. ISEKAI Core and Unit artifacts remain authoritative. The Adapter does not perform high-risk actions.

## Compatibility

The verified CLI baseline is `codex 0.146.0`. It is an observed test version, not a minimum requirement. After a CLI upgrade, rerun the Codex plugin manifest/command-surface checks and the Core status/resume/verify Golden Path smoke. Keep the plugin path unless Codex changes its documented plugin discovery contract.
