# ISEKAI Project Runtime

This directory is the host-neutral contract and generated Skill source for the project-local ISEKAI AI-DLC Runtime.

## Runtime adapters

The same host-neutral ISEKAI contract is exposed through three independent runtime adapters:

| Runtime | Project-local surface | Local test |
|---|---|---|
| Kiro | `.kiro/skills/isekai/SKILL.md` | invoke `/isekai` in Kiro |
| Claude Code | `.claude/skills/isekai/` | invoke `/isekai` |
| Codex | `.agents/skills/isekai/` | invoke `$isekai` |

Install the project-local launcher and adapters from an immutable Git tag first:

```bash
curl -fsSLo /tmp/isekai-install.sh https://raw.githubusercontent.com/dotwin7/isekai/v0.3.0/scripts/install.sh
bash /tmp/isekai-install.sh --source https://github.com/dotwin7/isekai.git --ref v0.3.0 --path . --runtime all --init
./.isekai/bin/isekai doctor --path .
```

The user-facing CLI uses direct actions through that launcher:

```bash
./.isekai/bin/isekai init --path path/to/project --profile software-delivery-profile
./.isekai/bin/isekai on
./.isekai/bin/isekai off
./.isekai/bin/isekai status
./.isekai/bin/isekai unit-migrate --project . --unit path/to/unit
./.isekai/bin/isekai intake --project . --source direct-request --goal "Add a feature"
./.isekai/bin/isekai verify --unit path/to/unit
```

Run the Agent CLI from a Project root containing `project.json`; Core also searches ancestors and unambiguous descendant workspace candidates. `init` creates a validated manifest and `units/` without overwriting existing configuration. Unit output defaults to the selected Project root.

The Adapter is discoverable by the host but conversation mode is off by default. Discovery, installation, cache presence, repository contents, and command text quoted in prose are not invocations. While mode is off, only an intentional runtime command invokes one explicit action. `on` activates later automatic routing and reports any Project-scoped `active_unit_binding` without selecting a new Unit. `unit-init` or `resume` binds one unfinished Unit until the final Operation Decision transitions it to `learned`; follow-up additions and changes use `amend` in that Unit. Core blocks new routing, new Unit creation, and persistent sibling-Unit actions while bound. `off` stops automatic routing without changing artifacts, checkpoints, or the binding. Other explicit actions remain available as one-shot calls while mode is off but remain subject to the Core binding. The bootstrap installer writes the repo/project/workspace Skills, Project-local Core, and selected Runtime's Project-local execution guard in one flow. It does not create marketplace registrations or modify user-global host settings.

The selected host agent is the adaptive workflow driver. The Runtime Skill tells it to interpret Core's machine-readable `workflow` directive, inspect the project, and propose a plan bounded by `maximum_agent_level`. ISEKAI does not add a second agent brain or lifecycle hook. The Project execution guard makes the Host read-only and connects the Project-local Core MCP gateway as the exclusive writer.

The ISEKAI Catalog groups the versioned functions provided by ISEKAI on the Project-local Core MCP control plane. Core exposes a digest-bound Catalog and includes it in new Unit Context Receipts. AI-DLC is the currently active entry. Future functions are delivered as independent ISEKAI Catalog packages and registered in the same Catalog. Inspect it with `isekai runtime catalog-status`.

`<PROJECT_ROOT>/.isekai/bin/isekai runtime <action>` is the internal Runtime Adapter contract. Adapters must resolve it from the selected Project and never use a global executable fallback.

Git releases are pinned by `isekai.lock.json`. Check and apply an update separately so contract changes are reviewable:

```bash
./.isekai/bin/isekai update --check --ref v0.3.0
./.isekai/bin/isekai update --ref v0.3.0
./.isekai/bin/isekai rollback
```

Ordinary updates preserve the pinned Foundation. `--include-foundation` and, when switching an existing Project path, `--adopt-foundation` are explicit contract-change operations. Adapter/Core compatibility is verified through `isekai runtime handshake`; Codex and Claude use the new Adapter from a new conversation.


```bash
./.isekai/bin/isekai runtime release-check --foundation foundation
```

After a human records an approved Foundation Decision and passing release Evidence, promotion is explicit:

```bash
./.isekai/bin/isekai runtime foundation-decision --foundation foundation --outcome approved --summary "..." --decided-by human-owner
./.isekai/bin/isekai runtime foundation-evidence --foundation foundation --passed --checks-json '[{"id":"all-tests","passed":true,"details":"pytest and Foundation validation passed"}]' --scope "..." --recorded-by validator
./.isekai/bin/isekai runtime foundation-promote --foundation foundation
```

The promotion command rejects missing approval or failing Evidence and does not mutate the Foundation on failure.

Verification Evidence preserves command exit codes and output digests. `prove` executes and receipts a verification command inside Core using a fail-closed macOS Seatbelt or Linux Bubblewrap sandbox: only declared system/runtime roots and the disposable workspace are readable, writes stay in that workspace, and network access is denied. Windows has no local provider; validate a Windows Project by running the same ISEKAI Core `prove` on a supported Linux or macOS environment. A host-only or external-CI result cannot replace the Core receipt. Each Evidence request passes the unique `authorization_id`; Core derives the actual command, status, output digest, and completion time from that same-stage receipt. Core rejects pre-Construction, unapproved, unexecuted, non-test, reused, stale, incomplete, or caller-mismatched grants. `verify` audits every historical Evidence record against its authorization-ledger prefix and record digest. Release Decisions bind the current passing Evidence ID and digest, and lifecycle completion rejects unchecked acceptance criteria, missing artifacts, blockers, or pending work.

Agent execution is bounded by a Unit-specific Execution Envelope. An agent may propose the scope, stages, depth, disposition, reason, allowed actions, forbidden actions, and iteration budget; an approved Inception Decision binds the Envelope ID and digest before Construction. Depth is `light`, `standard`, or `deep`. A stage with `disposition: skip` must state a reason and cannot allow actions. Runtime adapters use `artifact-write`, `managed-edit`, and `prove`; Core canonicalizes every target, validates optimistic digests, executes the action, and records its receipt in `execution-authorizations.json`. Free-standing `authorize edit|test` calls are denied.

One Core-bound Unit is active for Project persistent work until it reaches `learned`. Every user addition, deletion, or behavior change before that boundary is recorded by `amend` with its affected Unit artifacts. Core appends a digest-bound Amendment Decision, rewinds the same Unit to the earliest required gate, invalidates stale Evidence, and requires the affected documents to change plus a fresh lifecycle Decision that references the amendment ID. Only an explicit request to start separate work, abandon the Unit, or switch Units permits the human-decision action `active-unit-detach`, which first requires a current Checkpoint and records requester and reason. Even a `**` Envelope cannot authorize `read`, `edit`, or `test` against the default Unit collection or a sibling Unit, including canonical Units under a custom Project-local output root. Project source, tests, and pinned Foundation/Profile/Extension context remain available within the Receipt and Envelope.

Reusable learning does not cross that boundary by reading another Unit. An operating or learned Unit proposes a digest-bound Project Knowledge candidate, a real human records the `knowledge` Decision, and `project-knowledge-promote` appends the approved release. Future Units pin the release digest and only active entries conservatively overlapping their `work_scope`; an empty scope retains all active entries. Existing Units keep their creation-time snapshot. Project-level activation exposes only release metadata and counts, not every entry. `project-knowledge/` is Core-managed and an active Unit consumes only `context.project_knowledge` rather than reading the catalog directly. `project-knowledge-status` joins candidates to source Decision ledgers and reports pending-decision, approved, rejected, stale, promoted, or invalid, plus schema compatibility. Concurrent promotions from one base serialize to one winner; a failed catalog write restores the previous snapshot.

Human confirmation occurs when the complete Decision subject exists: after the autonomy-bounded plan and exact Envelope are presented, after Architecture is ready and before Validation, after passing Evidence and before Releasing, and after Operations review and before Learned. `status` and `resume` expose the next boundary as `human_gate`. A host tool permission, `dontAsk`, bypass mode, trust-all setting, or headless run is not a lifecycle Decision and cannot originate one. An ordinary user-requested change is an approved `amend`, not a rejected gate; after applying it, the Adapter presents the revised packet and obtains the new required gate Decision. If an approved scope, stage, risk, external effect, Envelope, or Evidence changes, the Adapter must ask again.

The adapters invoke the installed local launcher, which calls the shared Core dispatch contract internally:

```bash
./.isekai/bin/isekai runtime <action> ...
```

Intake accepts either a host Goal or a direct request and returns a normalized intent, a `query`, `quick-change`, or `unit` route, and a `direct-response`, `bounded-change`, or `adaptive-unit` workflow directive:

```bash
./.isekai/bin/isekai runtime intake --project . --source host-goal --goal "Add event classifier" --expected-outcome "Store classification and lineage" --scope 'src/events/**' --acceptance-criterion 'tests pass'
```

The current verified baseline is recorded in `compatibility.json` and can be inspected through:

```bash
./.isekai/bin/isekai runtime compatibility
```

`tested_versions` records live-observed CLI versions with linked evidence; it is not a minimum-version claim. Historical claims without linked raw evidence remain under `legacy_versions` and do not count as verified. The same response exposes the installed Core's `runtime_contract` and `trust_model`, so the no-high-risk and external-trust boundaries are machine-readable from a Project install. An unlisted CLI version is **unverified**, not automatically unsupported. A CLI upgrade does not move the Skill path unless the host's documented discovery contract changes. Before marking a new version verified, run the host validator when available plus the Core `status`, `resume`, and `verify` Golden Path smoke, and record the observation.

The three checked-in Runtime Skills are generated from `templates/runtime-skill.md`. Edit that template or `scripts/generate-runtime-skills.py`, regenerate, and verify drift with `python3 scripts/generate-runtime-skills.py --check`. `python3 scripts/runtime-host-check.py --runtime all` checks every source surface without requiring a host; add `--require-cli` for a selected Claude or Kiro CLI contract check.

## Core boundary

The adapters own runtime interaction only. ISEKAI Core owns Foundation resolution, Catalog validation and exposure, routing, the Project-scoped active Unit binding, Unit lifecycle, Decision boundaries, managed file/test execution, Evidence, and verification. Unit artifacts remain the source of truth; ignored `.isekai-runtime/active-unit.json` is enforcement state, not a lifecycle artifact. Core does not authenticate the reported human identity, create separate host agents, or execute undeclared external services and allowlisted external APIs on the caller's behalf.

## Non-goals

- No independent agent brain or model router
- No autonomous high-risk action
- No Runtime-owned Unit database
- No central registry or control plane in the MVP
- No prompt rewriting or tool-output compression
