"""Agent runners — launch and monitor agent CLI sessions."""

from .base import AgentRunner, RunResult
from .claude import ClaudeRunner
from .codex import CodexRunner
from .kiro import KiroRunner

RUNNERS: dict[str, type[AgentRunner]] = {
    "claude": ClaudeRunner,
    "codex": CodexRunner,
    "kiro": KiroRunner,
}

__all__ = [
    "RUNNERS",
    "AgentRunner",
    "ClaudeRunner",
    "CodexRunner",
    "KiroRunner",
    "RunResult",
]
