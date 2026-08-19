---
phase: operations
version: "0.1.0"
allowed_actions:
  - artifact-write
  - checkpoint
  - evidence
  - decision
  - project-knowledge-propose
  - project-knowledge-promote
checks: []
required_artifacts: []
---

# Operations

Operations covers the deployed state and the Learn transition. The Operation Decision gates the transition to `learned`.

## 1. Deployment monitoring

### Observation protocol

| Step | Action | Duration |
|---|---|---|
| Deploy | Execute the approved deployment plan | — |
| Smoke test | Run post-deployment verification from `release.md` | Immediate |
| Observation | Monitor metrics, logs, and alerts from `operations.md` | Per success criteria |
| Stability call | Compare observed state against success criteria | At observation period end |

### Incident handling during observation

1. If a rollback trigger condition from `operations.md` is met, execute the rollback plan immediately.
2. Record the incident as evidence with `evidence --scope "operations-incident"`.
3. If rollback was needed, the Unit cannot transition to `learned` without a fresh stability observation period.

## 2. Operation Decision

Present when the observation period ends and stability criteria are met:

| Section | Content |
|---|---|
| Observation summary | Duration, metrics observed, anomalies detected |
| Stability assessment | Met/not-met for each criterion in `operations.md` |
| Incidents | List of incidents during observation, resolution, and impact |
| Recommendation | Proceed to `learned` or extend observation with reason |

## 3. Knowledge harvest (Learn)

Before transitioning to `learned`, capture reusable learning from this Unit.

### What to capture

| Category | Examples |
|---|---|
| Patterns | Reusable code patterns, architecture decisions that worked well |
| Conventions | Naming, structure, or process conventions established during this Unit |
| Pitfalls | Dead ends, approaches that failed and why |
| Tools | New tools, commands, or configurations discovered |
| Domain knowledge | Business rules, constraints, or edge cases that future Units should know |

### Knowledge promotion flow

1. Identify candidates from the categories above.
2. `project-knowledge-propose` with structured entries and the proposing actor.
3. Present candidates to the user for the `knowledge` Decision.
4. After approval, `project-knowledge-promote` to make them available to future Units.

### What NOT to capture

- Implementation details that live in the code (read the code instead).
- Git history or commit messages (use `git log`).
- Ephemeral debugging steps (they belong in the Unit checkpoint history).
- Anything already documented in Foundation or existing Project Knowledge.

## 4. Profile-specific requirements

For projects with `security-profile`:
- Observation period must include at least one security-focused check (auth flow, permission boundaries).
- Incidents involving credential exposure or unauthorized access must be recorded even if resolved.
- Knowledge candidates must not contain secrets, credentials, or security-sensitive operational details.
