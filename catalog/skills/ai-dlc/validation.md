---
phase: validation
version: "0.1.0"
allowed_actions:
  - prove
  - artifact-write
  - checkpoint
  - evidence
  - decision
  - amend
checks:
  - id: tests
    kind: proof
    scope: tests
    required: true
required_artifacts: []
---

# Validation

The primary implementation must be complete before entering this phase. No new product-code implementation — only fixes for issues found during review and verification.

## 1. Review

### Review checklist

Review every changed file against these dimensions. Mark each as pass/issue-found/not-applicable.

| Dimension | What to check |
|---|---|
| Correctness | Does the code do what the acceptance criteria require? Trace input → output for each criterion. |
| Regression | Do existing tests still pass? Are there call sites or dependents that assume old behavior? |
| Edge cases | Empty inputs, boundary values, concurrent access, resource exhaustion, unicode, timezone. |
| Error handling | Are errors caught at the right level? Do error messages help diagnosis? No silent swallowing. |
| Compatibility | API contracts, data formats, configuration keys — anything external consumers rely on. |
| Trust boundaries | Where does untrusted data enter? Is it validated before use? |
| Authorization | Are permission checks present and correct at every entry point? |
| Input validation | Type checks, range checks, length limits, injection prevention. |
| Secret handling | No plaintext secrets in code, logs, error messages, or artifacts. |
| Test coverage | Is every acceptance criterion covered by at least one test? Are failure paths tested? |

### Profile-specific review

For projects with `security-profile`, add these dimensions:

| Dimension | What to check |
|---|---|
| OWASP Top 10 | Injection, broken auth, sensitive data exposure, XXE, broken access control, misconfig, XSS, insecure deserialization, vulnerable components, insufficient logging. |
| Credential flow | Secrets never appear in logs, artifacts, error messages, or Git history. |
| Injection vectors | SQL, command, LDAP, XPath, template injection — all inputs to interpreters are parameterized. |

### Finding classification

| Severity | Criteria | Action |
|---|---|---|
| Blocker | Incorrect behavior, data loss, security vulnerability | Must fix before transition |
| Warning | Missing edge case test, unclear error message | Fix or document as known limitation |
| Note | Style, naming, minor simplification | Record but do not block |

## 2. Evidence collection

### Verification plan execution

1. Run every verification command from the approved plan through `prove`.
2. Each `prove` runs in a disposable sandbox. No network, no source reads, no writes outside the workspace.
3. Record each result with `evidence`. Pass only the `evidence_command` object — Core derives the rest.
4. If a verification step fails, fix the issue (this is the one exception to "no new implementation"), then re-run.

### Evidence sufficiency criteria

Evidence is sufficient when all of these hold:

| Criterion | How to verify |
|---|---|
| All acceptance criteria covered | Each numbered criterion in `acceptance.md` maps to at least one passing `prove` result |
| Failure paths tested | At least one test per named failure case from inception |
| Broad regression | Full test suite passes (not just focused tests) |
| No test gaps | If a changed module has untested public functions, add tests or document the gap |

### Proof check enforcement

The `checks` in this phase's frontmatter (`tests` proof) are enforced by Core at transition time. Attempting to transition out of validation without passing test evidence will return a `pending_evidence` error listing the unsatisfied checks.

## 3. Decision Packet

### Required contents

The Decision Packet presented for the Architecture Decision must contain:

| Section | Content |
|---|---|
| Summary | One paragraph: what changed and why |
| Changed artifacts | List of Unit documents that were created or modified |
| Scope delta | Any deviation from the approved scope, with justification |
| Tradeoffs | What was gained and what was sacrificed |
| Risks | Residual risks after implementation |
| Evidence summary | Number of tests, pass rate, coverage highlights |
| Open items | Known limitations, deferred work, monitoring needs |

### Gate interaction

1. If `human_gate.confirmation_required` is true, present the full packet and stop.
2. An explicit rejection records a `rejected` Decision. Do not relabel it as an amendment.
3. Requested changes use `amend`, which rewinds the Unit to the required phase.
4. After applying amendments, present a fresh packet with the amendment ID and changed artifacts.
