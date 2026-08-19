---
phase: construction
version: "0.1.0"
allowed_actions:
  - managed-edit
  - prove
  - artifact-write
  - checkpoint
  - authorize
  - evidence
  - decision
  - amend
checks: []
required_artifacts:
  - architecture.md
  - implementation-guide.md
---

# Construction

All product-code mutations go through `managed-edit`. All verification goes through `prove`. No direct host file writes.

## 1. Implementation discipline

### Batch sizing

1. One `managed-edit` batch = one logical change (a function, a test, a config entry).
2. Never batch unrelated changes. If a batch touches files in two unrelated modules, split it.
3. Maximum 5 files per batch unless they are mechanically coupled (e.g., a rename across imports).

### Edit-verify-checkpoint cycle

```
managed-edit (one logical change)
  → prove (focused test covering the change)
  → artifact-write (update architecture.md or implementation-guide.md if structure changed)
  → checkpoint (record completed item, next action)
  → repeat
```

Do not accumulate multiple edits before verifying. Each edit must be independently verifiable before the next.

### Checkpoint frequency

Checkpoint is mandatory before:
- Any progress report to the user
- A Human Gate presentation
- Context or Unit switch
- Final response or intentional stop
- `checkpoint_required: true` from `managed-edit` or `prove`

### When to broaden verification

Run the full test suite (not just focused tests) when:
- A shared utility, configuration, or type definition changed
- More than 3 files changed since the last full run
- The change touches an interface boundary (API, schema, protocol)

## 2. Diagnosis

When a test fails or unexpected behavior occurs during construction:

### Hypothesis discipline

1. Form at least three competing hypotheses before investigating.
2. For each hypothesis, name one piece of evidence that would confirm it and one that would refute it.
3. Collect evidence in order of cheapest to obtain.
4. Eliminate hypotheses as evidence arrives. Do not anchor on the first plausible cause.

### Diagnosis steps

| Step | Action | Gate |
|---|---|---|
| Reproduce | Find the smallest input/scenario that triggers the failure | Must succeed before investigating |
| Isolate | Binary search: narrow to the module, then function, then line | Do not fix until the exact cause is confirmed |
| Root cause | Trace the causal chain past the symptom to the origin | Ask: would this fix prevent recurrence, or just mask it? |
| Fix | Smallest scoped change at the root cause | No unrelated cleanup |
| Regression | Add a test that fails before the fix and passes after | Mandatory when the failure was in existing behavior |
| Verify | Rerun the reproducer, focused tests, and affected broad tests | All must pass |

### Recovery from managed-edit failure

If `managed-edit` fails due to `expected_digest` mismatch:
1. Re-read the file to get the current digest.
2. Do not blindly retry with the new digest — inspect what changed.
3. If another process changed the file, reconcile before retrying.

## 3. Architecture documentation

During construction, materialize:

- `architecture.md`: component boundaries, data flow, key design decisions with rationale.
- `implementation-guide.md`: file-by-file change description, function signatures, test mapping.

These must be current before requesting the Architecture Decision. Present them as part of the Decision Packet.

### Profile-specific requirements

For projects with `security-profile`:
- Trace security-relevant code paths end to end in `architecture.md`.
- Document input validation, output encoding, and authorization checks for each boundary crossing.
- Verify that fixes and new code do not widen the attack surface.
