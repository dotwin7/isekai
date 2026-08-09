# ISEKAI Kiro runtime adapter

The first ISEKAI runtime adapter uses Kiro CLI's documented workspace Agent Skills surface.

## Installation

The versioned Kiro Skill source is checked in under the project runtime:

```text
runtime/adapters/kiro/skills/isekai/SKILL.md
```

`isekai install --runtime kiro` copies that source into the consuming Project's workspace discovery path:

```text
.kiro/skills/isekai/SKILL.md
```

The installer verifies the Git release and refuses to replace an unmanaged existing Skill. Updates verify the installed digest against `isekai.lock.json` before replacement.

Kiro discovers the Skill from `.kiro/skills/` and exposes it as `/isekai`. Skill slash commands require Kiro CLI `2.1.0` or newer. The Adapter requires the selected Project launcher, never falls back to a global executable, verifies its version/protocol handshake, and invokes the local ISEKAI Runtime contract:

```bash
<PROJECT_ROOT>/.isekai/bin/isekai runtime <action> ...
```

No Kiro hook, MCP server, prompt rewriter, or autonomous high-risk tool execution is required for this MVP.

## Conversation mode

The Adapter is discoverable but ISEKAI mode is off by default in each new conversation. Discovery, Skill installation, a leftover cache, repository contents, and a textual mention of `/isekai` are not activation and must not trigger the Skill. Only an intentional `/isekai <action>` command is a one-shot invocation while mode is off. `/isekai on [--project PATH]` activates Project-level intake routing for later requests and lists Unit candidates without selecting or resuming one. `/isekai resume [--project PATH] [--unit PATH]` separately restores a Unit. `/isekai off` stops routing without writing artifacts or checkpoints. These commands do not install, unload, enable, or disable the Kiro Skill itself.

Kiro headless mode does not provide interactive slash commands. For a single non-interactive action, begin the request with an exact first non-blank line such as `ISEKAI_HEADLESS: status --project .`. The marker applies only to that headless request. A headless run cannot originate a human Decision; it must stop at `human_gate.confirmation_required` unless an authenticated external approval is supplied by the surrounding system.

## Project bootstrap

Start Kiro from a repository containing `project.json` and invoke `/isekai on` without a path. Core searches the current directory, ancestors, and unambiguous descendant candidates. If no manifest exists, `/isekai init --path PATH` creates a validated manifest and Project-local `units/` after explicit confirmation. Multiple candidates require user selection. Unit metadata is shareable; sensitive raw Evidence belongs under ignored `units/**/evidence/raw/`.

## Supported surface

- Read-only: `on`, `off`, `status`, `intake`, `route`, `inception`, `resume`, `verify`, `release-check`
- Explicit writes: `init`, `unit-init`, `checkpoint`, `envelope-propose`, `authorize`, `evidence`, `decision`, `transition`, `foundation-decision`, `foundation-evidence`, `foundation-promote`
- The initial explicit request covers only a bounded Quick Change. Unit writes require autonomy-bounded plan approval, and `human_gate` identifies the Inception, Architecture, Release, or Operation Decision that blocks the next transition.

The runtime adapter does not own Unit state. Before a governed read, edit, test, or L2 development/test API call, `authorize` records a bounded grant in the Unit authorization ledger. L2 uses only an opaque `secret://provider/name` reference resolved by the host; raw credentials, production, deployment, and arbitrary high-risk remote actions remain prohibited. Kiro `read`, `write`, or `shell` permission prompts are tool permissions and do not replace a lifecycle Decision; do not use `/tools trust-all` or `--trust-all-tools` as approval evidence. Unit artifacts and ISEKAI Core remain authoritative.

## Compatibility

There is currently no linked live-verified Kiro CLI baseline. Kiro CLI `2.16.2`, installed through the official current-stable installer in GitHub Actions, passed the workspace Skill, version floor, headless, and selective tool-trust contract checks. It remains `validation-only` because no authenticated model session ran. The former `kiro-cli 2.14.2` repository claim is retained only as `legacy_versions` because no raw smoke record is linked. Inspect the shared matrix with `isekai runtime compatibility`; after a CLI upgrade, run `python scripts/runtime-host-check.py --runtime kiro --require-cli` and the opt-in `scripts/live-smoke.py --runtime kiro --host kiro`, then record the Evidence. Change the Skill path only if Kiro changes its documented discovery contract.
