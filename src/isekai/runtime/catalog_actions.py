from __future__ import annotations

from typing import Any, Callable, Mapping

from isekai.workflow.catalog import load_catalog

from .request_fields import RuntimeContractError


CatalogActionHandler = Callable[[Mapping[str, Any]], dict[str, Any]]


def active_catalog_action(
    entry_id: str,
    action: str,
    handler: CatalogActionHandler,
) -> CatalogActionHandler:
    """Bind an entry-owned handler to its installed active manifest contract."""

    def guarded(values: Mapping[str, Any]) -> dict[str, Any]:
        entry = next(
            (
                item
                for item in load_catalog().get("entries", [])
                if isinstance(item, dict) and item.get("id") == entry_id
            ),
            None,
        )
        if entry is None:
            raise RuntimeContractError(f"Catalog entry is not installed: {entry_id}")
        if entry.get("active") is not True:
            raise RuntimeContractError(f"Catalog entry is not active: {entry_id}")
        actions = entry.get("actions")
        if not isinstance(actions, list) or action not in actions:
            raise RuntimeContractError(
                f"Catalog entry {entry_id} does not declare action: {action}"
            )
        return handler(values)

    return guarded
