---
phase: release
version: "0.1.0"
allowed_actions:
  - artifact-write
  - checkpoint
  - authorize
  - evidence
  - decision
  - amend
checks:
  - id: release-evidence
    kind: proof
    scope: release-verification
    required: true
required_artifacts:
  - release.md
---

# Release

Release prepares the verified implementation for deployment. The Release Decision must be bound to current passing Evidence.

## 1. Release documentation

### release.md structure

| Section | Content |
|---|---|
| Version | Semantic version and changelog summary |
| Scope | What changed — features, fixes, breaking changes |
| Dependencies | New, updated, or removed dependencies with justification |
| Migration | Steps required for consumers to adopt the new version |
| Rollback | Exact steps to revert if the release fails |
| Deployment prerequisites | Environment, configuration, or infrastructure changes needed before deploy |
| Verification | Post-deployment smoke tests and success criteria |

### operations.md structure

If the Operations stage applies to this Unit:

| Section | Content |
|---|---|
| Monitoring | What metrics, logs, or alerts to watch after deployment |
| Incident response | Escalation path and rollback trigger conditions |
| Success criteria | Time-bound observation period and thresholds for "stable" |
| Handoff | Who owns the deployed change after the Unit completes |

## 2. Release verification

### Pre-release checklist

| Check | Criteria |
|---|---|
| All acceptance criteria | Every item in `acceptance.md` is checked and has passing evidence |
| Full test suite | Broad regression passes, not just focused tests |
| Documentation current | `architecture.md`, `implementation-guide.md`, `release.md` reflect final state |
| Breaking changes declared | If any public API, schema, or behavior changed, it's in the changelog |
| Rollback tested | If rollback requires steps beyond `git revert`, those steps are verified |

### Release Decision Packet

Present before requesting the Release Decision:

| Section | Content |
|---|---|
| Summary | What is being released and why |
| Evidence binding | Reference to the passing verification evidence this Decision covers |
| Scope delta | Any deviation from the approved scope |
| Risk assessment | Residual risks, deployment risks, rollback confidence |
| Post-release plan | Monitoring period, success criteria, escalation path |

The Release Decision must reference the current Evidence digest. If Evidence changes after the Decision, a fresh Decision is required.

## 3. Profile-specific requirements

For projects with `security-profile`:
- Release notes must include security-relevant changes with CVE references if applicable.
- Verify that no new credential, secret, or key appears in the release artifacts.
- Rollback plan must account for security-sensitive state (tokens, sessions, permissions).
