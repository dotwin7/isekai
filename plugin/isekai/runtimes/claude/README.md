# ISEKAI Claude Code adapter

This adapter follows Claude Code's documented plugin layout:

```text
plugin/isekai/runtimes/claude/
├── .claude-plugin/plugin.json
└── skills/isekai/SKILL.md
```

The plugin manifest remains available for package validation and local development:

```bash
claude --plugin-dir ./plugin/isekai/runtimes/claude
```

For a Project install, `isekai install --runtime claude` keeps the complete thin Plugin and marketplace under `.isekai/marketplaces/claude/`. It merges a project-scoped `extraKnownMarketplaces` and `enabledPlugins` declaration into `.claude/settings.json`; it does not write user-global Claude settings. Claude still applies its repository trust and Plugin-install consent boundary. The source, resolved Git commit, version, and digest are pinned in `isekai.lock.json`.

Invoke the namespaced Plugin Skill in the Claude Code session:

```text
/isekai-agent-plugin:isekai on --project path/to/project.json
/isekai-agent-plugin:isekai off
```

The Adapter is discoverable but ISEKAI mode is off by default in each new conversation. Discovery, Plugin installation, a leftover cache, repository contents, and a textual mention of the command are not activation and must not trigger the Skill. `disable-model-invocation: true` prevents Claude from loading it automatically. Only an intentional `/isekai-agent-plugin:isekai <action>` command is a one-shot invocation while mode is off. Its `on [--project PATH]` action activates Project-level intake routing for later requests and lists Unit candidates without selecting or resuming one. `resume [--project PATH] [--unit PATH]` separately restores a Unit. `off` stops routing without writing artifacts or checkpoints; it does not enable, disable, install, or unload the Claude Plugin.

Start Claude Code from a repository containing `project.json` to use `on` without a path. Core searches ancestors and unambiguous descendant candidates. If no manifest exists, the `init --path PATH` action creates a validated manifest and Project-local `units/` after explicit confirmation; multiple candidates require user selection. Sensitive raw Evidence belongs under ignored `units/**/evidence/raw/`.

The Skill requires the selected Project launcher `.isekai/bin/isekai`, never falls back to a global executable, performs a version/protocol handshake, and then calls the ISEKAI Plugin contract. ISEKAI Core and Unit artifacts remain authoritative. Before a governed read, edit, or test, `authorize` supplies a Project target and records a bounded grant in the Unit authorization ledger. The Adapter does not perform high-risk actions.

## Compatibility

The verified CLI baseline is `claude 2.1.220`. It is an observed test version, not a minimum requirement. After a CLI upgrade, run `claude plugin validate ./plugin/isekai/runtimes/claude` and the Core status/resume/verify Golden Path smoke. Keep the plugin path unless Claude changes its documented plugin discovery contract.
