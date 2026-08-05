---
description: Run the ISEKAI AI-DLC workflow for project routing, inception questions, Unit resume, artifact verification, and explicit Unit initialization or checkpoint updates.
---

# ISEKAI for Claude Code

Use the ISEKAI Core through the repository-local interface. This plugin is a thin Claude Code adapter; it is not an independent agent brain and has no Ouroboros dependency.

The adapter is discoverable by Claude Code, but ISEKAI workflow mode is off by default in every new conversation.

- `/isekai-agent-plugin:isekai on [--project PATH]` activates ISEKAI for the current conversation and loads Project/Foundation context plus Unit candidate paths. It never selects or resumes a Unit; use the namespaced `resume [--project PATH] [--unit PATH]` action for Unit restoration.
- `/isekai-agent-plugin:isekai off` invokes `isekai plugin off`, stops automatic ISEKAI routing, and never changes Unit artifacts or writes a checkpoint.
- An explicit namespaced action runs once while mode is off without activating persistent conversation mode.

While mode is active, normalize each new request through `intake` and follow its Query, Quick Change, or Unit route. Mode is conversation-local and separate from Unit lifecycle status. In a new or interrupted session, invoke `on` to activate the Project, then invoke `resume` separately only when continuing an existing Unit.

The user invokes this skill as `/isekai-agent-plugin:isekai $ARGUMENTS`. Use an explicit project path when supplied; otherwise let Core discover `project.json` from the current directory, ancestors, or descendant workspace candidates. If none exists, explain `isekai init` and get explicit user confirmation before initializing. If multiple candidates are reported, present every path and ask the user to choose one. `unit-init` without `--output` uses the selected Project root's `units/`; relative outputs are also Project-relative.

Use the installed ISEKAI Plugin launcher from the repository or project environment:

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

Rules:

1. Run `status` or `route` before a persistent change.
2. In `inception`, ask the returned questions and summarize intent, scope, acceptance criteria, risks, and non-goals before writing.
3. Get explicit user confirmation before `unit-init`, `checkpoint`, lifecycle transitions, or other writes.
4. Use `resume` after a new session and treat Unit artifacts, Receipt, Checkpoint, Decisions, and Evidence as authoritative.
5. Run `verify` after implementation and report its actual result.
6. Do not execute remote Git, cloud, Kubernetes, credential, customer-data, or high-risk security actions through this plugin.
7. Do not inject the entire Foundation or conversation into context.
