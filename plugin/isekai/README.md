# ISEKAI Agent Plugin

ISEKAI Agent Plugin is a standalone runtime integration for the ISEKAI AI-DLC Core. It is not Ouroboros and has no Ouroboros dependency.

## Runtime adapters

The same host-neutral ISEKAI contract is exposed through three independent runtime adapters:

| Runtime | Project-local surface | Local test |
|---|---|---|
| Kiro | `.kiro/skills/isekai/SKILL.md` | invoke `/isekai` in Kiro |
| Claude Code | `.isekai/marketplaces/claude/` + `.claude/settings.json` | invoke `/isekai-agent-plugin:isekai` |
| Codex | `.isekai/marketplaces/codex/` + `.agents/plugins/marketplace.json` | invoke `$isekai-agent-plugin:isekai` |

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
./.isekai/bin/isekai intake --source direct-request --goal "Add a feature"
./.isekai/bin/isekai verify --unit path/to/unit
```

Run the Agent CLI from a Project root containing `project.json`; Core also searches ancestors and unambiguous descendant workspace candidates. `init` creates a validated manifest and `units/` without overwriting existing configuration. Unit output defaults to the selected Project root.

The Adapter is discoverable by the host but conversation mode is off by default. Discovery, installation, cache presence, repository contents, and command text quoted in prose are not invocations. While mode is off, only an intentional runtime command invokes one explicit action. `on` alone activates later automatic routing for one conversation at Project scope and lists Unit candidates without selecting them. `resume` separately selects and restores a Unit. `off` stops automatic routing without changing artifacts or checkpoints. Other explicit actions remain available as one-shot calls while mode is off. Installation writes Plugin sources and declarations only inside the Project and never registers a Project path in user-global host settings.

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

Verification Evidence preserves command exit codes and output digests. Each command must reference the unique `authorization_id` returned by its immediately preceding `test` authorization in the same stage. Core rejects pre-Construction, unapproved, non-test, reused, or stale grants. When captured output is supplied, Core computes its SHA-256 digest before persisting the Evidence record. Release Decisions bind the current passing Evidence ID and digest, and lifecycle completion rejects unchecked acceptance criteria, missing artifacts, blockers, or pending work.

Agent execution is bounded by a Unit-specific Execution Envelope. An agent may propose the scope, stages, depth, allowed actions, forbidden actions, and iteration budget; an approved Inception Decision binds the Envelope ID and digest before Construction. Runtime adapters call `authorize` with a Project target before an action. Core canonicalizes that target, uses the Unit's actual phase, records each successful grant in `execution-authorizations.json`, and denies work after the iteration budget is exhausted.

The adapters invoke the installed local launcher, which calls the shared Core dispatch contract internally:

```bash
./.isekai/bin/isekai plugin <action> ...
```

Intake accepts either a host Goal or a direct request and returns a normalized intent plus `query`, `quick-change`, or `unit` routing result:

```bash
./.isekai/bin/isekai plugin intake --source host-goal --goal "Add event classifier" --expected-outcome "Store classification and lineage" --scope 'src/events/**' --acceptance-criterion 'tests pass'
```

The current verified baseline is recorded in `compatibility.json` and can be inspected through:

```bash
./.isekai/bin/isekai plugin compatibility
```

`tested_versions` records observed CLI versions with evidence; it is not a minimum-version claim. An unlisted CLI version is **unverified**, not automatically unsupported. A CLI upgrade does not move the plugin path unless the host's documented discovery contract changes. Before marking a new version verified, run the host validator when available plus the Core `status`, `resume`, and `verify` Golden Path smoke.

## Core boundary

The adapters own runtime interaction only. ISEKAI Core owns Foundation resolution, routing, Unit lifecycle, Decision boundaries, Evidence, and verification. Unit artifacts remain the source of truth.

## Non-goals

- No independent agent brain or model router
- No Ouroboros dependency
- No autonomous high-risk action
- No plugin-owned Unit database
- No central registry or control plane in the MVP
- No prompt rewriting or tool-output compression
