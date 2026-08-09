# ISEKAI Claude Code adapter

This adapter follows Claude Code's documented project Skill layout:

```text
runtime/adapters/claude/
└── skills/isekai/SKILL.md
```

For a Project install, `isekai install --runtime claude` copies the versioned Skill to `.claude/skills/isekai/` for direct project discovery and records its digest in `isekai.lock.json`. It does not modify `.claude/settings.json` or create marketplace registrations.

Invoke the project Skill in the Claude Code session:

```text
/isekai on --project path/to/project.json
/isekai off
```

The Adapter is discoverable but ISEKAI mode is off by default in each new conversation. Discovery, Skill installation, a leftover cache, and repository contents are not activation. A textual mention of a command is not activation either and must not trigger the Skill. `disable-model-invocation: true` prevents implicit loading. Only an intentional `/isekai <action>` is a one-shot invocation while mode is off. `on` activates Project-level intake routing for later requests, `resume` separately restores a Unit, and `off` stops routing without writing artifacts or checkpoints.

Start Claude Code from a repository containing `project.json` to use `on` without a path. Core searches ancestors and unambiguous descendant candidates. If no manifest exists, the `init --path PATH` action creates a validated manifest and Project-local `units/` after explicit confirmation; multiple candidates require user selection. Sensitive raw Evidence belongs under ignored `units/**/evidence/raw/`.

The Skill requires the selected Project launcher `.isekai/bin/isekai`, never falls back to a global executable, performs a version/protocol handshake, and then calls the ISEKAI Runtime contract. ISEKAI Core and Unit artifacts remain authoritative. Before a governed read, edit, test, or L2 development/test API call, `authorize` records a bounded grant in the Unit authorization ledger. L2 uses only an opaque `secret://provider/name` reference resolved by the host; raw credentials, production, deployment, and arbitrary high-risk remote actions remain prohibited.

Claude's Bash or file permission prompt authorizes a tool invocation, not a lifecycle Decision. Before `decision`, `foundation-decision`, or `foundation-promote`, the Adapter presents the Decision Packet and its bound Envelope or Evidence and waits for an explicit user response. `status` and `resume` expose the next required Decision as `human_gate`; a non-interactive or bypass-permission session cannot invent that approval.

## Compatibility

There is currently no linked live-verified Claude CLI baseline. Local CLI `2.1.224` passed the project Skill source contract check, but no authenticated model session was available, so it remains `validation-only`. The former `2.1.220` repository claim is retained only as `legacy_versions` because no raw smoke record is linked. After a CLI upgrade, run `python scripts/runtime-host-check.py --runtime claude --require-cli` and `scripts/live-smoke.py --runtime claude --host claude`, then record the Evidence.
