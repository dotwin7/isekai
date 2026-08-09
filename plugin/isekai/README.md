# ISEKAI Agent Plugin

ISEKAI Agent Plugin is a standalone runtime integration for the ISEKAI AI-DLC Core. It is not Ouroboros and has no Ouroboros dependency.

## Runtime adapters

The same host-neutral ISEKAI contract is exposed through three independent runtime adapters:

| Runtime | Project-local surface | Local test |
|---|---|---|
| Kiro | `.kiro/skills/isekai/SKILL.md` | invoke `/isekai` in Kiro |
| Claude Code | `.claude/skills/isekai/` + `.isekai/marketplaces/claude/` | invoke `/isekai` |
| Codex | `.agents/skills/isekai/` + `.isekai/marketplaces/codex/` | invoke `$isekai` |

Install the project-local launcher and adapters from an immutable Git tag first:

```bash
curl -fsSLo /tmp/isekai-install.sh https://raw.githubusercontent.com/dotwin7/isekai/v0.1.0/scripts/install.sh
bash /tmp/isekai-install.sh --source https://github.com/dotwin7/isekai.git --ref v0.1.0 --path . --runtime all --init
./.isekai/bin/isekai doctor --path .
```

The user-facing CLI uses direct actions through that launcher:

```bash
./.isekai/bin/isekai init --path path/to/project --profile software-delivery-profile
./.isekai/bin/isekai on
./.isekai/bin/isekai off
./.isekai/bin/isekai status
./.isekai/bin/isekai unit-migrate --project . --unit path/to/unit
./.isekai/bin/isekai intake --source direct-request --goal "Add a feature"
./.isekai/bin/isekai verify --unit path/to/unit
```

Run the Agent CLI from a Project root containing `project.json`; Core also searches ancestors and unambiguous descendant workspace candidates. `init` creates a validated manifest and `units/` without overwriting existing configuration. Unit output defaults to the selected Project root.

The Adapter is discoverable by the host but conversation mode is off by default. Discovery, installation, cache presence, repository contents, and command text quoted in prose are not invocations. While mode is off, only an intentional runtime command invokes one explicit action. `on` alone activates later automatic routing for one conversation at Project scope and lists Unit candidates without selecting them. `resume` separately selects and restores a Unit. `off` stops automatic routing without changing artifacts or checkpoints. Other explicit actions remain available as one-shot calls while mode is off. Installation writes Plugin packages, repo/project Skills, and declarations only inside the Project and never registers a Project path in user-global host settings.

The selected host agent is the adaptive workflow driver. The Runtime Skill tells it to interpret Core's machine-readable `workflow` directive, inspect the project, and propose a Level-1 plan. ISEKAI does not add a second agent brain, required hook, or resident harness.

`<PROJECT_ROOT>/.isekai/bin/isekai plugin <action>` is the internal Runtime Adapter contract. Adapters must resolve it from the selected Project and never use a global executable fallback.

Git releases are pinned by `isekai.lock.json`. Check and apply an update separately so contract changes are reviewable:

```bash
./.isekai/bin/isekai update --check --ref v0.2.0
./.isekai/bin/isekai update --ref v0.2.0
./.isekai/bin/isekai rollback
```

Ordinary updates preserve the pinned Foundation. `--include-foundation` and, when switching an existing Project path, `--adopt-foundation` are explicit contract-change operations. Adapter/Core compatibility is verified through `isekai plugin handshake`; Codex and Claude use the new Adapter from a new conversation.


```bash
./.isekai/bin/isekai plugin release-check --foundation foundation
```

After a human records an approved Foundation Decision and passing release Evidence, promotion is explicit:

```bash
./.isekai/bin/isekai plugin foundation-decision --foundation foundation --outcome approved --summary "..." --decided-by human-owner
./.isekai/bin/isekai plugin foundation-evidence --foundation foundation --passed --checks-json '[{"id":"all-tests","passed":true,"details":"pytest and Foundation validation passed"}]' --scope "..." --recorded-by validator
./.isekai/bin/isekai plugin foundation-promote --foundation foundation
```

The promotion command rejects missing approval or failing Evidence and does not mutate the Foundation on failure.

Verification Evidence preserves host-reported command exit codes and output digests. Each command must reference the unique `authorization_id` returned by its immediately preceding `test` authorization in the same stage. Core rejects pre-Construction, unapproved, non-test, reused, or stale grants. When captured output is supplied, Core computes its SHA-256 digest before persisting the Evidence record; when only a digest is supplied, Core validates the attestation structure but does not independently rerun the command. New records disclose that distinction in `attestation.output_digest_verification`. Release Decisions bind the current passing Evidence ID and digest, and lifecycle completion rejects unchecked acceptance criteria, missing artifacts, blockers, or pending work.

Agent execution is bounded by a Unit-specific Execution Envelope. An agent may propose the scope, stages, depth, disposition, reason, allowed actions, forbidden actions, and iteration budget; an approved Inception Decision binds the Envelope ID and digest before Construction. Depth is `light`, `standard`, or `deep`. A stage with `disposition: skip` must state a reason and cannot allow actions. Runtime adapters call `authorize` with a Project target before an action. Core canonicalizes that target, uses the Unit's actual phase, records each successful grant in `execution-authorizations.json`, and denies work after the iteration budget is exhausted.

Human confirmation occurs when the complete Decision subject exists: after the Level-1 plan and exact Envelope are presented, after Architecture is ready and before Validation, after passing Evidence and before Releasing, and after Operations review and before Learned. `status` and `resume` expose the next boundary as `human_gate`. A host tool permission, `dontAsk`, bypass mode, trust-all setting, or headless run is not a lifecycle Decision and cannot originate one. If an approved scope, stage, risk, external effect, Envelope, or Evidence changes, the Adapter must ask again.

The adapters invoke the installed local launcher, which calls the shared Core dispatch contract internally:

```bash
./.isekai/bin/isekai plugin <action> ...
```

Intake accepts either a host Goal or a direct request and returns a normalized intent, a `query`, `quick-change`, or `unit` route, and a `direct-response`, `bounded-change`, or `adaptive-unit` workflow directive:

```bash
./.isekai/bin/isekai plugin intake --source host-goal --goal "Add event classifier" --expected-outcome "Store classification and lineage" --scope 'src/events/**' --acceptance-criterion 'tests pass'
```

The current verified baseline is recorded in `compatibility.json` and can be inspected through:

```bash
./.isekai/bin/isekai plugin compatibility
```

`tested_versions` records live-observed CLI versions with linked evidence; it is not a minimum-version claim. Historical claims without linked raw evidence remain under `legacy_versions` and do not count as verified. The same response exposes the installed Core's `plugin_contract` and `trust_model`, so the no-high-risk and external-trust boundaries are machine-readable from a Project install. An unlisted CLI version is **unverified**, not automatically unsupported. A CLI upgrade does not move the plugin path unless the host's documented discovery contract changes. Before marking a new version verified, run the host validator when available plus the Core `status`, `resume`, and `verify` Golden Path smoke, and record the observation.

The three checked-in Runtime Skills are generated from `templates/runtime-skill.md`. Edit that template or `scripts/generate-runtime-skills.py`, regenerate, and verify drift with `python3 scripts/generate-runtime-skills.py --check`. `python3 scripts/runtime-host-check.py --runtime all` checks every source surface without requiring a host; add `--require-cli` for a selected Claude or Kiro CLI contract check.

## Core boundary

The adapters own runtime interaction only. ISEKAI Core owns Foundation resolution, routing, Unit lifecycle, Decision boundaries, Evidence, and verification. Unit artifacts remain the source of truth. The manifest `trust_model` and digest-bound Decision/Evidence attestations explicitly state that Core does not execute host commands or authenticate the reported human identity.

## Non-goals

- No independent agent brain or model router
- No Ouroboros dependency
- No autonomous high-risk action
- No plugin-owned Unit database
- No central registry or control plane in the MVP
- No prompt rewriting or tool-output compression
