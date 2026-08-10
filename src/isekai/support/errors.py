from __future__ import annotations


class WorkflowError(ValueError):
    """Base for all ISEKAI workflow errors.

    Inherits ValueError so existing ``except ValueError`` handlers remain
    compatible during the transition period.
    """


class PreflightError(WorkflowError):
    """Unit or artifact preflight checks failed."""


class LifecycleError(WorkflowError):
    """Invalid lifecycle transition, missing Decision, or gate violation."""


class IntegrityError(WorkflowError):
    """Digest chain, ledger, or artifact binding mismatch."""


class AuthorizationError(WorkflowError):
    """Execution Envelope or action authorization rejected."""


class EvidenceError(WorkflowError):
    """Verification Evidence recording or validation failed."""
