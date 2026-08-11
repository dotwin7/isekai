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

The bootstrap install with `--runtime kiro` creates the `isekai-core` custom agent with only `read` and `@mcp` and connects the Project Core MCP server in the same flow. `doctor --fix` repairs the Project execution guard when needed. Select that agent for ISEKAI work and start a new conversation. No lifecycle hook or prompt rewriter is installed.

## Conversation mode

The Adapter is discoverable but ISEKAI mode is off by default in each new conversation. Discovery, Skill installation, a leftover cache, repository contents, and a textual mention of `/isekai` are not activation and must not trigger the Skill. Only an intentional `/isekai <action>` command is a one-shot invocation while mode is off. `/isekai on [--project PATH]` activates Project-level intake routing for later requests and lists Unit candidates without selecting or resuming one. `/isekai resume [--project PATH] [--unit PATH]` separately restores a Unit. `/isekai off` stops routing without writing artifacts or checkpoints. These commands do not install, unload, enable, or disable the Kiro Skill itself.

`unit-init` or `resume` creates a Project-scoped Core binding that remains active until the final Operation Decision transitions the Unit to `learned`. Before then, Core blocks new routing, new Unit creation, and persistent sibling-Unit actions; follow-up additions and changes use `amend` in the same Unit. Only an explicit user decision to start separate work, abandon the Unit, or switch Units permits `active-unit-detach` after a current Checkpoint. `off` does not clear this binding.

Kiro headless mode does not provide interactive slash commands. For a single non-interactive action, begin the request with an exact first non-blank line such as `ISEKAI_HEADLESS: status --project .`. The marker applies only to that headless request. A headless run cannot originate a human Decision; it must stop at `human_gate.confirmation_required` unless an authenticated external approval is supplied by the surrounding system.

## Project bootstrap

Start Kiro from a repository containing `project.json` and invoke `/isekai on` without a path. Core searches the current directory, ancestors, and unambiguous descendant candidates. If no manifest exists, `/isekai init --path PATH` creates a validated manifest and Project-local `units/` after explicit confirmation. Multiple candidates require user selection. Unit metadata is shareable; sensitive raw Evidence belongs under ignored `units/**/evidence/raw/`.

## Supported surface

- Read-only: `on`, `off`, `status`, `intake`, `route`, `inception`, `resume`, `verify`, `release-check`
- Core-mediated writes: `init`, `unit-init`, `checkpoint`, `artifact-write`, `managed-edit`, `envelope-propose`, `evidence`, `amend`, `active-unit-detach`, `decision`, `transition`, `foundation-decision`, `foundation-evidence`, `foundation-promote`
- Core-mediated test execution: `prove`; free-standing `authorize edit|test` calls are denied
- The initial explicit request covers only a bounded Quick Change. Unit writes require autonomy-bounded plan approval, and `human_gate` identifies the Inception, Architecture, Release, or Operation Decision that blocks the next transition.

The runtime adapter does not own Unit state. The selected `isekai-core` agent has no direct write or shell tool: Unit documents, Project edits, and tests go through the Core MCP gateway, which authorizes and receipts them. L2 uses only an opaque `secret://provider/name` resolved by the host; raw credentials, production, deployment, and arbitrary high-risk remote actions remain prohibited. Do not use another agent profile or `/tools trust-all` to bypass this boundary. Unit artifacts and ISEKAI Core remain authoritative.

## Compatibility

There is currently no linked live-verified Kiro CLI baseline. Kiro CLI `2.16.2`, installed through the official current-stable installer in GitHub Actions, passed the workspace Skill, version floor, headless, and selective tool-trust contract checks. It remains `validation-only` because no authenticated model session ran. The former `kiro-cli 2.14.2` repository claim is retained only as `legacy_versions` because no raw smoke record is linked. Inspect the shared matrix with `isekai runtime compatibility`; after a CLI upgrade, run `python scripts/runtime-host-check.py --runtime kiro --require-cli` and the opt-in `scripts/live-smoke.py --runtime kiro --host kiro`, which resumes the directory-scoped session for an unhinted automatic intake and validates the completed-Unit Golden Path from `KIRO_ACP_RECORD_PATH` tool events rather than model prose before recording Evidence. Change the Skill path only if Kiro changes its documented discovery contract.
