---
phase: inception
version: "0.1.0"
allowed_actions:
  - intake
  - route
  - inception
  - unit-init
  - artifact-write
  - checkpoint
  - envelope-propose
  - envelope-approve
  - decision
checks: []
required_artifacts:
  - intent.md
  - requirements.md
  - acceptance.md
  - plan.md
---

# Inception

No product-code mutation is permitted during this phase. Only read-only investigation and Unit artifact writes are allowed.

## 1. Requirements clarification

### Clarity dimensions

Every dimension must reach "testable" before plan creation. Track each explicitly.

| Dimension | Testable when |
|---|---|
| Goal | One sentence, falsifiable outcome |
| Scope | Named files/modules/APIs, explicit exclusions |
| Users | Named actors with distinct needs |
| Behavior | Input → output pairs for normal and edge cases |
| Constraints | Named limits (performance, compatibility, dependencies) |
| Interfaces | Exact endpoints, schemas, or UI surfaces affected |
| Failure cases | At least three named failure modes with expected handling |
| Compatibility | Existing tests, APIs, data formats that must not break |
| Verification | Concrete commands or scenarios that prove completion |

### Question strategy

1. Ask 1-3 questions per round. Each question must resolve ambiguity that would change the plan.
2. Offer concrete alternatives with tradeoffs, not open-ended prompts.
3. When the user answers vaguely, restate your interpretation and ask for confirmation.
4. Do not treat silence as approval for irreversible choices.
5. Stop clarifying when every acceptance criterion maps to a concrete verification command.

### Profile-specific dimensions

For projects with `security-profile`, add these mandatory dimensions:

| Dimension | Testable when |
|---|---|
| Threat model | Named threat actors and attack vectors for changed surfaces |
| Trust boundaries | Data flow across boundary crossings identified |
| Credential flow | Where secrets enter, transit, and rest, with no plaintext exposure |

## 2. Plan creation

### Approach evaluation

1. Identify at least two viable approaches.
2. Score each on: change size, rollback difficulty, compatibility risk, performance impact, security exposure.
3. Choose the approach that minimizes total risk at the smallest change size.
4. Document the deciding reason and what would make the rejected approaches better.

### Plan structure

The plan presented for user approval must contain exactly these sections:

| Section | Content |
|---|---|
| Goal | One sentence from the clarified intent |
| Expected outcome | Observable state after completion |
| Scope | Files and modules in scope, explicit out-of-scope |
| Non-goals | What this Unit deliberately does not address |
| Acceptance criteria | Numbered, each with a verification command or scenario |
| Risks | Named risks with likelihood and mitigation |
| Stage plan | Each lifecycle stage with apply/skip, depth (light/standard/deep), and reason |
| Verification plan | Exact test commands, expected outputs, and coverage targets |

### Envelope preparation

Translate the approved stage plan into an Execution Envelope before `unit-init`. Each stage entry must carry `disposition` (apply/skip), `depth`, `reason`, and `allowed_actions` bounded to what the stage requires.

## 3. Artifact materialization

After explicit user approval and `unit-init`:

1. Persist all `required_artifacts` via `artifact-write` in a single batch.
2. `intent.md`: the clarified goal, scope, constraints, and acceptance criteria.
3. `requirements.md`: detailed functional and non-functional requirements.
4. `acceptance.md`: numbered checklist where each item maps to a verification command.
5. `plan.md`: the approved plan in the structure above.
6. Set Release and Operations dispositions.
7. Write the initial Checkpoint with the first pending action.
8. Verify no `ISEKAI:placeholder` markers remain in any artifact.
