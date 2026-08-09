# ISEKAI Codex adapter

This adapter follows Codex's documented repository Skill layout:

```text
runtime/adapters/codex/
└── skills/isekai/
    ├── SKILL.md
    └── agents/openai.yaml
```

Validate the checked-in Skill structure with the repository contract test:

```bash
python3 -m pytest -q tests/test_adapter_contract.py
```

The ISEKAI Project installer copies the versioned Skill to Codex's documented repo-local Skill location:

```text
.agents/skills/isekai/
```

`isekai install --runtime codex` records the repo Skill digest in `isekai.lock.json`. It does not create a repo marketplace declaration and never registers a Project path in user-global Codex configuration. `agents/openai.yaml` sets `allow_implicit_invocation: false`, so invoke the repo Skill explicitly as `$isekai`. Start a new conversation after installation or an Adapter update.

## Conversation mode

The Adapter is discoverable but ISEKAI mode is off by default in each new conversation. Discovery, Skill installation, a leftover cache, and repository contents are not activation. A textual mention of a command is not activation either and must not trigger the Skill. Invoke `$isekai on` from a Project root to activate Project-level intake routing for later requests and list Unit candidates without selecting or resuming one. Use `$isekai resume [--project PATH] [--unit PATH]` separately to restore a Unit. `$isekai off` stops routing without writing artifacts or checkpoints.

Core searches the current directory, ancestors, and unambiguous descendant candidates. If no manifest exists, `$isekai init --path PATH` creates a validated manifest and Project-local `units/` after explicit confirmation; multiple candidates require user selection. Sensitive raw Evidence belongs under ignored `units/**/evidence/raw/`.

The Skill requires the selected Project launcher `.isekai/bin/isekai`, never falls back to a global executable, performs a version/protocol handshake, and then calls the ISEKAI Runtime contract. ISEKAI Core and Unit artifacts remain authoritative. Before a governed read, edit, or test, `authorize` supplies a Project target and records a bounded grant in the Unit authorization ledger. The Adapter does not perform high-risk actions.

## Compatibility

The live-verified CLI baseline is `codex 0.147.0`; it is an observed test version, not a minimum requirement. It passed injected repo Skill activation, two-turn automatic intake, and the Core status/resume/verify Golden Path smoke. The former `0.146.0` repository claim is retained only as `legacy_versions` because no raw smoke record is linked. After a CLI upgrade, rerun `scripts/live-smoke.py --runtime codex --host codex` plus the completed-Unit Golden Path and record the Evidence. Keep `.agents/skills/isekai` unless Codex changes its documented repo Skill discovery contract.
