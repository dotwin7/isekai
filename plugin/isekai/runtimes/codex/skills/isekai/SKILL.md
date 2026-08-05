---
name: isekai
description: Run the ISEKAI AI-DLC workflow for project routing, version compatibility, installation health, safe updates, Unit resume, and artifact verification.
---

# ISEKAI for Codex

Use the ISEKAI Core through the repository-local interface. This plugin is a thin Codex adapter; it is not an independent agent brain and has no Ouroboros dependency.

This Adapter is version `0.1.0` and uses protocol `1.0.0`. Prefer the Project launcher at `.isekai/bin/isekai` on POSIX or `.isekai/bin/isekai.cmd` on Windows; fall back to an installed `isekai` command only when the Project launcher is absent. Before `on`, `status`, or `resume`, run `plugin handshake --runtime codex --adapter-version 0.1.0 --protocol-version 1.0.0 --project PATH` with that launcher and stop on incompatibility.

The adapter is discoverable by Codex, but ISEKAI workflow mode is off by default in every new conversation.

- Invoke `$isekai on [--project PATH]` to activate ISEKAI for the current conversation and load Project/Foundation context plus Unit candidate paths. It never selects or resumes a Unit; use `$isekai resume [--project PATH] [--unit PATH]` for Unit restoration.
- Invoke `$isekai off` to run `isekai plugin off`, stop automatic ISEKAI routing, and preserve Unit artifacts and checkpoints unchanged.
- An explicit skill action runs once while mode is off without activating persistent conversation mode.

While mode is active, normalize each new request through `intake` and follow its Query, Quick Change, or Unit route. Mode is conversation-local and separate from Unit lifecycle status. In a new or interrupted session, invoke `on` to activate the Project, then invoke `resume` separately only when continuing an existing Unit.

Use an explicit project path when supplied; otherwise let Core discover `project.json` from the current directory, ancestors, or descendant workspace candidates. If none exists, explain `isekai init` and get explicit user confirmation before initializing. If multiple candidates are reported, present every path and ask the user to choose one. `unit-init` without `--output` uses the selected Project root's `units/`; relative outputs are also Project-relative.

Use the installed ISEKAI Plugin launcher from the repository or project environment:

```bash
isekai plugin <action> ...
```

Supported actions:

```text
init [--path PATH] [--id ID] [--foundation-path PATH] [--profile ID ...] [--document-language ko|en] [--maximum-agent-level LEVEL]
handshake --runtime codex --adapter-version VERSION --protocol-version VERSION [--project PATH]
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
envelope-propose --unit PATH --scope PATH [--scope PATH ...] --stages-json JSON --allowed-action ACTION [--allowed-action ACTION ...] --max-iterations N --proposed-by ACTOR
authorize --unit PATH --action ACTION [--target PATH] [--stage STAGE]
evidence --unit PATH --passed --commands-json JSON --scope TEXT --recorded-by ACTOR [--notes TEXT]
decision --unit PATH --gate GATE --outcome approved|rejected --summary TEXT --rationale TEXT [--alternatives-json JSON] [--tradeoff TEXT] [--risk TEXT] [--reference TEXT] --decided-by HUMAN
transition --unit PATH --to STATUS
verify --unit PATH
```

Project installation management uses the top-level launcher rather than `isekai plugin`:

```text
install --source GIT --ref TAG [--path PATH] [--runtime all|kiro|claude|codex] [--adopt-foundation] [--register]
doctor [--path PATH]
update --check --ref TAG [--path PATH] [--include-foundation]
update --ref TAG [--path PATH] [--include-foundation] [--adopt-foundation] [--register]
rollback [--path PATH] [--register]
```

Rules:

1. Run `status` or `route` before a persistent change.
2. In `inception`, ask the returned questions and summarize intent, scope, acceptance criteria, risks, and non-goals before writing.
3. Get explicit user confirmation before `install`, applying `update`, `rollback`, `unit-init`, `checkpoint`, lifecycle transitions, or other writes. Run read-only `update --check` before applying an update.
4. Use `resume` after a new session and treat Unit artifacts, Receipt, Checkpoint, Decisions, and Evidence as authoritative.
5. Run `verify` after implementation and report its actual result.
6. Do not execute arbitrary remote Git, cloud, Kubernetes, credential, customer-data, or high-risk security actions through this plugin. Installation may use only a source explicitly supplied by the user; updates must use the Git source pinned in `isekai.lock.json` unless the user explicitly approves a source change.
7. Do not inject the entire Foundation or conversation into context.
8. Preserve the pinned Foundation during ordinary updates. Use `--include-foundation` only after showing the contract change and receiving explicit human approval. Start a new conversation after a Codex or Claude Adapter update.
