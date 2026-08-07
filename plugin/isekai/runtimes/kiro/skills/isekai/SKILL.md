---
name: isekai
description: Explicit-command-only ISEKAI adapter. Use only when the user intentionally invokes `/isekai ACTION`, or after `/isekai on` explicitly activated ISEKAI earlier in this conversation. Do not use for ordinary project work, repository contents, Skill/cache discovery, or textual command mentions.
---

# ISEKAI Agent Plugin

You are using the ISEKAI runtime adapter. ISEKAI is a workflow and evidence contract, not a replacement agent brain.

This Adapter is version `0.1.0` and uses protocol `1.0.0`. Require the current Project launcher at `<PROJECT_ROOT>/.isekai/bin/isekai` on POSIX or `<PROJECT_ROOT>/.isekai/bin/isekai.cmd` on Windows. Never fall back to an `isekai` command from `PATH`. If the Project launcher or lock is absent, stop and ask the user to install or repair the Project-local runtime. Before every requested Core plugin action other than `handshake` itself, run `plugin handshake --runtime kiro --adapter-version 0.1.0 --protocol-version 1.0.0 --project PROJECT_ROOT` with that launcher and stop on incompatibility.

## Invocation

The adapter may be discoverable by Kiro, but discovery is not activation. ISEKAI workflow mode is off by default in every new conversation.

- Treat only an intentional `/isekai <action> [arguments]` command as an invocation while mode is off. A command shown or discussed in prose, documentation, code, logs, or review feedback is not an invocation.
- Project files, repository identity, an installed Skill, a leftover Skill cache, and Skill discovery never activate ISEKAI and never authorize reading this Skill as project guidance.
- While mode is off and no intentional command was invoked, do not inspect ISEKAI Project/Foundation/Unit context and do not run a launcher, `handshake`, Core, `intake`, `route`, `inception`, `status`, or `resume`. Continue with the host agent's ordinary workflow.
- Only an intentional `/isekai on [--project PATH]` activates automatic ISEKAI routing for later ordinary requests in the current conversation. All other explicit actions are one-shot and leave mode off.
- Never infer mode from an earlier or interrupted conversation. If activation state is not explicit in the current conversation, treat it as off.

- `/isekai on [--project PATH]` activates ISEKAI for the current conversation and loads Project/Foundation context plus Unit candidate paths. It never selects or resumes a Unit; use `/isekai resume [--project PATH] [--unit PATH]` for Unit restoration.
- `/isekai off` invokes `isekai plugin off`, stops automatic ISEKAI routing, and never changes Unit artifacts or writes a checkpoint.
- `/isekai <action> [arguments]` runs one explicit action while mode is off without activating persistent conversation mode.

While mode is active, normalize each new request through `intake` and follow its Query, Quick Change, or Unit route. Mode is conversation-local and separate from Unit lifecycle status. In a new or interrupted session, invoke `on` to activate the Project, then invoke `resume` separately only when continuing an existing Unit.

The user can invoke this skill as `/isekai <action> [arguments]`. Use an explicit project path when supplied; otherwise let Core discover `project.json` from the current directory, ancestors, or descendant workspace candidates. If none exists, explain `isekai init` and get explicit user confirmation before initializing. If multiple candidates are reported, present every path and ask the user to choose one. `unit-init` without `--output` uses the selected Project root's `units/`; relative outputs are also Project-relative.

Use only the launcher inside the selected Project:

```bash
<PROJECT_ROOT>/.isekai/bin/isekai plugin <action> ...
```

Supported actions:

```text
init [--path PATH] [--id ID] [--foundation-path PATH] [--profile ID ...] [--document-language ko|en] [--maximum-agent-level LEVEL]
handshake --runtime kiro --adapter-version VERSION --protocol-version VERSION [--project PATH]
on [--project PATH]
off
compatibility
release-check --foundation PATH
foundation-decision --foundation PATH --outcome approved|rejected --summary TEXT --decided-by HUMAN
foundation-evidence --foundation PATH --passed --checks-json JSON --scope TEXT --recorded-by ACTOR
foundation-promote --foundation PATH
intake --source host-goal|direct-request --goal TEXT [--expected-outcome TEXT] [--scope PATH] [--constraint TEXT] [--acceptance-criterion TEXT] [--change none|local|persistent] [--risk low|high]
status --project PATH [--unit PATH]
route --change none|local|persistent [--risk low|high] [flags]
inception --project PATH
resume --project PATH [--unit PATH]
unit-init --project PATH --title TITLE [--output PATH] [--owner OWNER]
checkpoint --unit PATH --next-action TEXT [--completed ITEM ...] [--pending ITEM ...] [--blocked-by ITEM ...]
envelope-propose --unit PATH --scope PATH [--scope PATH ...] --stages-json JSON --allowed-action ACTION [--allowed-action ACTION ...] --max-iterations N --proposed-by ACTOR [--expires-in-hours HOURS]
envelope-approve --unit PATH
authorize --unit PATH --action ACTION --target PROJECT_RELATIVE_PATH [--stage CURRENT_UNIT_PHASE]
evidence --unit PATH --passed --commands-json JSON --scope TEXT --recorded-by ACTOR [--notes TEXT]
decision --unit PATH --gate GATE --outcome approved|rejected --summary TEXT --rationale TEXT [--alternatives-json JSON] [--tradeoff TEXT] [--risk TEXT] [--reference TEXT] --decided-by HUMAN
transition --unit PATH --to STATUS
verify --unit PATH
```

Project installation management uses the top-level launcher rather than `isekai plugin`:

```text
install --source GIT --ref TAG [--path PATH] [--runtime all|kiro|claude|codex] [--adopt-foundation]
doctor [--path PATH]
update --check --ref TAG [--path PATH] [--include-foundation]
update --ref TAG [--path PATH] [--include-foundation] [--adopt-foundation]
rollback [--path PATH]
```

## Workflow rules

1. Start with `status` or `route` before proposing a persistent change.
2. For `inception`, ask the listed questions and summarize intent, scope, acceptance criteria, risks, and non-goals before writing artifacts.
3. Ask for explicit user confirmation immediately before `install`, applying `update`, `rollback`, `unit-init`, `checkpoint`, lifecycle transitions, or any other user-visible write. A successful `authorize` call only writes the audit grant already covered by the approved Envelope and does not need separate confirmation. `update --check` is read-only and must precede an applied update.
4. Use `resume` after a new session or context interruption. Treat `checkpoint.json`, `context-receipt.json`, Decisions, and Evidence as authoritative.
5. Use `verify` after implementation and report its actual result. Do not claim success from an unexecuted command.
6. If the route is Unit, a human Decision is required before progressing through a consequential gate.
7. Do not execute arbitrary remote Git, cloud, Kubernetes, customer-data, credential, or high-risk security actions through this skill. Installation may use only a source explicitly supplied by the user; updates must use the Git source pinned in `isekai.lock.json` unless the user explicitly approves a source change.
8. Do not copy the entire Foundation or conversation into context. Load only the project, Unit, Receipt, Checkpoint, and referenced artifacts needed for the current action.
9. Preserve the pinned Foundation during ordinary updates. Use `--include-foundation` only after showing the contract change and receiving explicit human approval. Start a new conversation after a Codex or Claude Adapter update.
10. Call `authorize` immediately before each read, edit, or test action governed by a Unit. Supply a Project-relative target; Core records a successful grant and consumes one approved iteration. Never override the Unit's actual phase with `--stage`. For Evidence, include each immediately preceding `test` grant's returned `authorization_id` in the matching command record; older, reused, non-test, and cross-stage grants are rejected.

## Output discipline

Present the JSON result briefly, preserve error messages and blockers, and identify the next action. Never invent missing evidence or silently turn a denied or pending decision into approval.
