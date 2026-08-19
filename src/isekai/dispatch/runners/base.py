"""Base agent runner interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    exit_code: int = 0
    error: str | None = None


class AgentRunner:
    """Abstract base for agent CLI launchers."""

    name: str = "base"

    def build_command(
        self,
        project_root: Path,
        *,
        model: str | None = None,
        prompt: str | None = None,
        max_turns: int = 50,
    ) -> list[str]:
        raise NotImplementedError

    def run(
        self,
        project_root: Path,
        *,
        model: str | None = None,
        prompt: str | None = None,
        max_turns: int = 50,
    ) -> RunResult:
        import subprocess

        cmd = self.build_command(
            project_root, model=model, prompt=prompt, max_turns=max_turns,
        )
        try:
            completed = subprocess.run(cmd, cwd=project_root, check=False)
            return RunResult(exit_code=completed.returncode)
        except FileNotFoundError:
            return RunResult(exit_code=127, error=f"{self.name} CLI not found")
        except OSError as exc:
            return RunResult(exit_code=1, error=str(exc))

    def is_available(self) -> bool:
        import shutil
        return shutil.which(self.name) is not None
