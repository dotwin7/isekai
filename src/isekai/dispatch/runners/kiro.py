"""Kiro agent runner."""

from __future__ import annotations

from pathlib import Path

from .base import AgentRunner


class KiroRunner(AgentRunner):
    name = "kiro"

    def build_command(
        self,
        project_root: Path,
        *,
        model: str | None = None,
        prompt: str | None = None,
        max_turns: int = 50,
    ) -> list[str]:
        cmd = ["kiro"]
        if prompt:
            cmd.append(prompt)
        return cmd
