from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..support.files import UnsafeControlFile, inspect_tree_beneath
from .release import DistributionError, LEGACY_PLUGIN_ID, MANAGED_ROOT


CODEX_REPO_MARKETPLACE = Path(".agents/plugins/marketplace.json")
CLAUDE_PROJECT_SETTINGS = Path(".claude/settings.json")


class MarketplaceValidationOperations(Protocol):
    def launcher_issues(self, managed: Path) -> list[str]: ...

    def codex_marketplace_manifest(
        self, marketplace_name: str, *, managed: bool = False
    ) -> dict[str, Any]: ...

    def claude_marketplace_manifest(
        self, marketplace_name: str, version: str
    ) -> dict[str, Any]: ...

    def read_control_json(
        self, path: Path, *, root: Path, label: str
    ) -> dict[str, Any]: ...

    def find_plugin(self, plugins: list[Any]) -> tuple[int | None, Any]: ...

    def codex_plugin_entry(self, *, managed: bool) -> dict[str, Any]: ...


def managed_control_issues(
    project_root: Path,
    lock: dict[str, Any],
    operations: MarketplaceValidationOperations,
) -> list[str]:
    """Validate generated launchers and host metadata not covered by adapter digests."""
    managed = project_root / MANAGED_ROOT
    try:
        inspect_tree_beneath(managed, label="managed installation")
    except UnsafeControlFile as exc:
        return [str(exc)]
    issues = operations.launcher_issues(managed)
    adapters = lock.get("adapters")
    if not isinstance(adapters, dict):
        return issues
    selected = {
        runtime
        for runtime, legacy_path in {
            "codex": f"{MANAGED_ROOT}/marketplaces/codex/plugins/{LEGACY_PLUGIN_ID}",
            "claude": f"{MANAGED_ROOT}/marketplaces/claude/plugins/{LEGACY_PLUGIN_ID}",
        }.items()
        if isinstance(adapters.get(runtime), dict)
        and adapters[runtime].get("path") == legacy_path
    }
    if not selected:
        return issues
    marketplace_name = lock.get("marketplace")
    if not isinstance(marketplace_name, str) or not marketplace_name.strip():
        return [*issues, "lock marketplace must be a non-empty string"]

    expected_documents: list[tuple[str, Path, dict[str, Any]]] = []
    if "codex" in selected:
        expected_documents.append(
            (
                "codex",
                project_root
                / MANAGED_ROOT
                / "marketplaces/codex/.agents/plugins/marketplace.json",
                operations.codex_marketplace_manifest(marketplace_name),
            )
        )
    if "claude" in selected:
        entry = adapters.get("claude")
        version = entry.get("version") if isinstance(entry, dict) else None
        if not isinstance(version, str) or not version:
            issues.append("claude adapter lock has no version for marketplace validation")
        else:
            expected_documents.append(
                (
                    "claude",
                    project_root
                    / MANAGED_ROOT
                    / "marketplaces/claude/.claude-plugin/marketplace.json",
                    operations.claude_marketplace_manifest(marketplace_name, version),
                )
            )
    for runtime, path, expected in expected_documents:
        try:
            actual = operations.read_control_json(
                path,
                root=project_root,
                label=f"{runtime} marketplace metadata",
            )
        except DistributionError as exc:
            issues.append(str(exc))
            continue
        if actual != expected:
            issues.append(f"{runtime} marketplace metadata mismatch")
    if "codex" in selected:
        try:
            document = operations.read_control_json(
                project_root / CODEX_REPO_MARKETPLACE,
                root=project_root,
                label="Codex repo marketplace",
            )
            plugins = document.get("plugins")
            if not isinstance(plugins, list):
                raise DistributionError("Codex repo marketplace plugins must be a list")
            _, entry = operations.find_plugin(plugins)
            if entry != operations.codex_plugin_entry(managed=True):
                issues.append("Codex repo marketplace ISEKAI entry mismatch")
        except DistributionError as exc:
            issues.append(str(exc))
    if "claude" in selected:
        try:
            settings = operations.read_control_json(
                project_root / CLAUDE_PROJECT_SETTINGS,
                root=project_root,
                label="Claude project settings",
            )
            marketplaces = settings.get("extraKnownMarketplaces")
            enabled = settings.get("enabledPlugins")
            expected_marketplace = {
                "source": {
                    "source": "directory",
                    "path": f"./{MANAGED_ROOT}/marketplaces/claude",
                }
            }
            plugin_key = f"{LEGACY_PLUGIN_ID}@{marketplace_name}"
            if not isinstance(marketplaces, dict) or (
                marketplaces.get(marketplace_name) != expected_marketplace
            ):
                issues.append("Claude project marketplace declaration mismatch")
            if not isinstance(enabled, dict) or enabled.get(plugin_key) is not True:
                issues.append("Claude project plugin enablement mismatch")
        except DistributionError as exc:
            issues.append(str(exc))
    return issues


__all__ = ["MarketplaceValidationOperations", "managed_control_issues"]
