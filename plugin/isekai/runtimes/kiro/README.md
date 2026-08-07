# ISEKAI Kiro runtime adapter

The first ISEKAI runtime adapter uses Kiro CLI's documented workspace Agent Skills surface.

## Installation

The versioned Kiro Skill source is checked in under the plugin runtime:

```text
plugin/isekai/runtimes/kiro/skills/isekai/SKILL.md
```

`isekai install --runtime kiro` copies that source into the consuming Project's workspace discovery path:

```text
.kiro/skills/isekai/SKILL.md
```

The installer verifies the Git release and refuses to replace an unmanaged existing Skill. Updates verify the installed digest against `isekai.lock.json` before replacement.

Kiro discovers the Skill from `.kiro/skills/` and exposes it as `/isekai`. The Adapter requires the selected Project launcher, never falls back to a global executable, verifies its version/protocol handshake, and invokes the local ISEKAI Plugin contract:

```bash
<PROJECT_ROOT>/.isekai/bin/isekai plugin <action> ...
```

No Kiro hook, MCP server, prompt rewriter, or autonomous high-risk tool execution is required for this MVP.

## Conversation mode

The Adapter is discoverable but ISEKAI mode is off by default in each new conversation. Discovery, Skill installation, a leftover cache, repository contents, and a textual mention of `/isekai` are not activation and must not trigger the Skill. Only an intentional `/isekai <action>` command is a one-shot invocation while mode is off. `/isekai on [--project PATH]` activates Project-level intake routing for later requests and lists Unit candidates without selecting or resuming one. `/isekai resume [--project PATH] [--unit PATH]` separately restores a Unit. `/isekai off` stops routing without writing artifacts or checkpoints. These commands do not install, unload, enable, or disable the Kiro Skill itself.

## Project bootstrap

Start Kiro from a repository containing `project.json` and invoke `/isekai on` without a path. Core searches the current directory, ancestors, and unambiguous descendant candidates. If no manifest exists, `/isekai init --path PATH` creates a validated manifest and Project-local `units/` after explicit confirmation. Multiple candidates require user selection. Unit metadata is shareable; sensitive raw Evidence belongs under ignored `units/**/evidence/raw/`.

## Supported surface

- Read-only: `on`, `off`, `status`, `intake`, `route`, `inception`, `resume`, `verify`, `release-check`
- Explicit writes: `init`, `unit-init`, `checkpoint`, `envelope-propose`, `authorize`, `evidence`, `decision`, `transition`, `foundation-decision`, `foundation-evidence`, `foundation-promote`
- Human confirmation is required before write actions or any lifecycle decision.

The runtime adapter does not own Unit state. Before a governed read, edit, or test, `authorize` supplies a Project target and records a bounded grant in the Unit authorization ledger. Unit artifacts and ISEKAI Core remain authoritative.

## Compatibility

The verified CLI baseline is `kiro-cli 2.14.2`. It is an observed test version, not a minimum requirement. Inspect the shared matrix with `isekai plugin compatibility`; after a CLI upgrade, rerun the Core status/resume/verify Golden Path smoke and only change the skill path if Kiro changes its documented discovery contract.
