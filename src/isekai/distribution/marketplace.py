from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

from .release import (
    MANAGED_ROOT,
    PLUGIN_ID,
    DistributionError,
    _read_json,
    _write_json_atomic,
)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "project"


def _project_id(project_root: Path) -> str:
    manifest = project_root / "project.json"
    if manifest.is_file():
        value = _read_json(manifest).get("id")
        if isinstance(value, str) and value.strip():
            return value
    return project_root.name


def _replace_tree(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)


def _copy_managed_root(source: Path, destination: Path) -> None:
    def ignore(path: str, names: list[str]) -> set[str]:
        if Path(path).resolve() == source.resolve():
            return {"rollback"} & set(names)
        return set()

    shutil.copytree(source, destination, ignore=ignore)


def _write_launchers(managed: Path) -> None:
    binary = managed / "bin"
    binary.mkdir(parents=True, exist_ok=True)
    python_launcher = binary / "isekai.py"
    python_launcher.write_text(
        "from pathlib import Path\n"
        "import sys\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))\n"
        "from isekai.cli import main\n\n"
        "raise SystemExit(main())\n",
        encoding="utf-8",
    )
    posix_launcher = binary / "isekai"
    posix_launcher.write_text(
        "#!/bin/sh\nexec python3 \"$(dirname \"$0\")/isekai.py\" \"$@\"\n",
        encoding="utf-8",
    )
    posix_launcher.chmod(0o755)
    (binary / "isekai.cmd").write_text(
        "@py -3 \"%~dp0isekai.py\" %*\r\n",
        encoding="utf-8",
    )


def _codex_cachebuster(plugin_root: Path, commit: str) -> str:
    path = plugin_root / ".codex-plugin/plugin.json"
    manifest = _read_json(path)
    base = str(manifest.get("version", "")).split("+", 1)[0]
    if not base:
        raise DistributionError("Codex plugin manifest has no version")
    token = re.sub(r"[^0-9A-Za-z-]", "-", commit[:12]) or "local"
    version = f"{base}+codex.{token}"
    manifest["version"] = version
    _write_json_atomic(path, manifest)
    return version


def _prepare_codex_marketplace(
    managed: Path,
    adapter_source: Path,
    marketplace_name: str,
    commit: str,
) -> tuple[Path, str]:
    root = managed / "marketplaces/codex"
    plugin_root = root / "plugins" / PLUGIN_ID
    _replace_tree(adapter_source, plugin_root)
    installed_version = _codex_cachebuster(plugin_root, commit)
    _write_json_atomic(
        root / ".agents/plugins/marketplace.json",
        {
            "name": marketplace_name,
            "interface": {"displayName": f"ISEKAI ({marketplace_name})"},
            "plugins": [
                {
                    "name": PLUGIN_ID,
                    "source": {
                        "source": "local",
                        "path": f"./plugins/{PLUGIN_ID}",
                    },
                    "policy": {
                        "installation": "AVAILABLE",
                        "authentication": "ON_INSTALL",
                    },
                    "category": "Productivity",
                }
            ],
        },
    )
    return plugin_root, installed_version


def _prepare_claude_marketplace(
    managed: Path,
    adapter_source: Path,
    marketplace_name: str,
    version: str,
) -> Path:
    root = managed / "marketplaces/claude"
    plugin_root = root / "plugins" / PLUGIN_ID
    _replace_tree(adapter_source, plugin_root)
    _write_json_atomic(
        root / ".claude-plugin/marketplace.json",
        {
            "name": marketplace_name,
            "owner": {"name": "ISEKAI"},
            "description": "Project-local ISEKAI AI-DLC plugins",
            "plugins": [
                {
                    "name": PLUGIN_ID,
                    "source": f"./plugins/{PLUGIN_ID}",
                    "description": "ISEKAI AI-DLC workflow integration for Claude Code",
                    "version": version,
                }
            ],
        },
    )
    return plugin_root

def _registration_commands(
    project_root: Path,
    marketplace_name: str,
    runtimes: Iterable[str],
    *,
    update: bool,
) -> list[list[str]]:
    commands: list[list[str]] = []
    selected = set(runtimes)
    if "codex" in selected:
        marketplace = project_root / MANAGED_ROOT / "marketplaces/codex"
        if not update:
            commands.append(["codex", "plugin", "marketplace", "add", str(marketplace)])
        commands.append(["codex", "plugin", "add", f"{PLUGIN_ID}@{marketplace_name}"])
    if "claude" in selected:
        marketplace = project_root / MANAGED_ROOT / "marketplaces/claude"
        if update:
            commands.append(
                [
                    "claude",
                    "plugin",
                    "update",
                    f"{PLUGIN_ID}@{marketplace_name}",
                    "--scope",
                    "project",
                ]
            )
        else:
            commands.append(
                [
                    "claude",
                    "plugin",
                    "marketplace",
                    "add",
                    str(marketplace),
                    "--scope",
                    "project",
                ]
            )
            commands.append(
                [
                    "claude",
                    "plugin",
                    "install",
                    f"{PLUGIN_ID}@{marketplace_name}",
                    "--scope",
                    "project",
                ]
            )
    return commands


def _run_registration(commands: list[list[str]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in commands:
        if shutil.which(command[0]) is None:
            raise DistributionError(f"host CLI is not installed: {command[0]}")
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        result = {
            "command": command,
            "exit_code": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
        results.append(result)
        if completed.returncode != 0:
            raise DistributionError(
                f"host registration failed ({' '.join(command)}): "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
    return results
