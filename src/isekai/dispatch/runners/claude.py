"""Claude Code agent runner."""

from __future__ import annotations

from pathlib import Path

from .base import AgentRunner


class ClaudeRunner(AgentRunner):
    name = "claude"

    def build_command(
        self,
        project_root: Path,
        *,
        model: str | None = None,
        prompt: str | None = None,
        max_turns: int = 50,
    ) -> list[str]:
        cmd = ["claude"]
        if prompt:
            cmd.extend(["-p", prompt])
            cmd.extend(["--max-turns", str(max_turns)])
        cmd.extend(["--project-dir", str(project_root)])
        if model:
            cmd.extend(["--model", model])
        return cmd
