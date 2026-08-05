---
name: isekai
description: Use the ISEKAI AI-DLC workflow for project routing, inception questions, Unit resume, artifact verification, and explicit Unit initialization or checkpoint updates.
---

# ISEKAI Agent Plugin

You are using the ISEKAI runtime adapter. ISEKAI is a workflow and evidence contract, not a replacement agent brain.

## Invocation

The adapter is discoverable by Kiro, but ISEKAI workflow mode is off by default in every new conversation.

- `/isekai on [--project PATH]` activates ISEKAI for the current conversation and loads Project/Foundation context plus Unit candidate paths. It never selects or resumes a Unit; use `/isekai resume [--project PATH] [--unit PATH]` for Unit restoration.
- `/isekai off` invokes `isekai plugin off`, stops automatic ISEKAI routing, and never changes Unit artifacts or writes a checkpoint.
- `/isekai <action> [arguments]` runs one explicit action while mode is off without activating persistent conversation mode.

While mode is active, normalize each new request through `intake` and follow its Query, Quick Change, or Unit route. Mode is conversation-local and separate from Unit lifecycle status. In a new or interrupted session, invoke `on` to activate the Project, then invoke `resume` separately only when continuing an existing Unit.

The user can invoke this skill as `/isekai <action> [arguments]`. Use an explicit project path when supplied; otherwise let Core discover `project.json` from the current directory, ancestors, or descendant workspace candidates. If none exists, explain `isekai init` and get explicit user confirmation before initializing. If multiple candidates are reported, present every path and ask the user to choose one. `unit-init` without `--output` uses the selected Project root's `units/`; relative outputs are also Project-relative.

Run the installed ISEKAI Plugin launcher from the repository or project environment:

```bash
isekai plugin <action> ...
```

Supported actions:

```text
init [--path PATH] [--id ID] [--foundation-path PATH] [--profile ID ...] [--document-language ko|en] [--maximum-agent-level LEVEL]
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

## Workflow rules

1. Start with `status` or `route` before proposing a persistent change.
2. For `inception`, ask the listed questions and summarize intent, scope, acceptance criteria, risks, and non-goals before writing artifacts.
3. Ask for explicit user confirmation immediately before `unit-init`, `checkpoint`, lifecycle transitions, or any other write.
4. Use `resume` after a new session or context interruption. Treat `checkpoint.json`, `context-receipt.json`, Decisions, and Evidence as authoritative.
5. Use `verify` after implementation and report its actual result. Do not claim success from an unexecuted command.
6. If the route is Unit, a human Decision is required before progressing through a consequential gate.
7. Do not execute remote Git, cloud, Kubernetes, customer-data, credential, or high-risk security actions through this skill.
8. Do not copy the entire Foundation or conversation into context. Load only the project, Unit, Receipt, Checkpoint, and referenced artifacts needed for the current action.

## Output discipline

Present the JSON result briefly, preserve error messages and blockers, and identify the next action. Never invent missing evidence or silently turn a denied or pending decision into approval.
