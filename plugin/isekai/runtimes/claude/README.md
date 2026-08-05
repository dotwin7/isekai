# ISEKAI Claude Code adapter

This adapter follows Claude Code's documented plugin layout:

```text
plugin/isekai/runtimes/claude/
├── .claude-plugin/plugin.json
└── skills/isekai/SKILL.md
```

Test locally without changing user or marketplace configuration:

```bash
claude --plugin-dir ./plugin/isekai/runtimes/claude
```

Invoke the namespaced Skill in the Claude Code session:

```text
/isekai-agent-plugin:isekai on --project path/to/project.json
/isekai-agent-plugin:isekai off
```

The Adapter is discoverable but ISEKAI mode is off by default in each new conversation. `on [--project PATH]` activates Project-level intake routing and lists Unit candidates without selecting or resuming one. The namespaced `resume [--project PATH] [--unit PATH]` action separately restores a Unit. `off` stops routing without writing artifacts or checkpoints; it does not enable, disable, install, or unload the Claude plugin.

Start Claude Code from a repository containing `project.json` to use `on` without a path. Core searches ancestors and unambiguous descendant candidates. If no manifest exists, the namespaced `init --path PATH` action creates a validated manifest and Project-local `units/` after explicit confirmation; multiple candidates require user selection. Sensitive raw Evidence belongs under ignored `units/**/evidence/raw/`.

The Skill calls the installed local ISEKAI Plugin launcher `isekai plugin`. ISEKAI Core and Unit artifacts remain authoritative. The Adapter does not install global configuration, register a marketplace, or perform high-risk actions.

## Compatibility

The verified CLI baseline is `claude 2.1.220`. It is an observed test version, not a minimum requirement. After a CLI upgrade, run `claude plugin validate ./plugin/isekai/runtimes/claude` and the Core status/resume/verify Golden Path smoke. Keep the plugin path unless Claude changes its documented plugin discovery contract.
