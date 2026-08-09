# ISEKAI Claude Code adapter

This adapter follows Claude Code's documented plugin layout:

```text
plugin/isekai/runtimes/claude/
├── .claude-plugin/plugin.json
└── skills/isekai/SKILL.md
```

The plugin manifest remains available for package validation and local development:

```bash
claude plugin validate ./plugin/isekai/runtimes/claude --strict
claude --plugin-dir ./plugin/isekai/runtimes/claude plugin list --json
```

For a Project install, `isekai install --runtime claude` keeps the complete thin Plugin and marketplace under `.isekai/marketplaces/claude/` and copies its Skill to `.claude/skills/isekai/` for direct project discovery. It merges project-scoped marketplace declarations into `.claude/settings.json` without writing user-global settings. Claude still applies its repository trust and Plugin-install consent boundary to the marketplace path. The Plugin package and project Skill have separate digests in `isekai.lock.json`.

Invoke the project Skill in the Claude Code session:

```text
/isekai on --project path/to/project.json
/isekai off
```

If the Plugin is separately installed from the marketplace, `/isekai-agent-plugin:isekai` is the namespaced alias.

The Adapter is discoverable but ISEKAI mode is off by default in each new conversation. Discovery, Plugin or Skill installation, a leftover cache, and repository contents are not activation. A textual mention of a command is not activation either and must not trigger the Skill. `disable-model-invocation: true` prevents implicit loading. Only an intentional `/isekai <action>` or namespaced Plugin alias is a one-shot invocation while mode is off. `on` activates Project-level intake routing for later requests, `resume` separately restores a Unit, and `off` stops routing without writing artifacts or checkpoints.

Start Claude Code from a repository containing `project.json` to use `on` without a path. Core searches ancestors and unambiguous descendant candidates. If no manifest exists, the `init --path PATH` action creates a validated manifest and Project-local `units/` after explicit confirmation; multiple candidates require user selection. Sensitive raw Evidence belongs under ignored `units/**/evidence/raw/`.

The Skill requires the selected Project launcher `.isekai/bin/isekai`, never falls back to a global executable, performs a version/protocol handshake, and then calls the ISEKAI Plugin contract. ISEKAI Core and Unit artifacts remain authoritative. Before a governed read, edit, or test, `authorize` supplies a Project target and records a bounded grant in the Unit authorization ledger. The Adapter does not perform high-risk actions.

Claude's Bash or file permission prompt authorizes a tool invocation, not a lifecycle Decision. Before `decision`, `foundation-decision`, or `foundation-promote`, the Adapter presents the Decision Packet and its bound Envelope or Evidence and waits for an explicit user response. `status` and `resume` expose the next required Decision as `human_gate`; a non-interactive or bypass-permission session cannot invent that approval.

## Compatibility

There is currently no linked live-verified Claude CLI baseline. Local CLI `2.1.224` passed strict source and installed-package validation and was discovered as an enabled inline Plugin with one `isekai` Skill, but no authenticated model session was available, so it remains `validation-only`. The former `2.1.220` repository claim is retained only as `legacy_versions` because no raw smoke record is linked. After a CLI upgrade, run `python scripts/runtime-host-check.py --runtime claude --require-cli` and `scripts/live-smoke.py --runtime claude --host claude`, then record the Evidence. Keep `.claude/skills/isekai` and the Plugin package unless Claude changes those documented contracts.
