from __future__ import annotations

from typing import Any, Mapping


class RuntimeContractError(ValueError):
    """Raised for invalid or unsafe Runtime Skill requests."""


_MISSING = object()


def list_field(payload: Mapping[str, Any], key: str) -> list[Any]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise RuntimeContractError(f"runtime request field {key} must be a list")
    return list(value)


def boolean_field(
    payload: Mapping[str, Any],
    key: str,
    *,
    default: bool | object = _MISSING,
) -> bool:
    value = payload.get(key, default)
    if value is _MISSING:
        raise RuntimeContractError(f"missing runtime request field: {key}")
    if not isinstance(value, bool):
        raise RuntimeContractError(f"runtime request field {key} must be boolean")
    return value


def string_field(
    payload: Mapping[str, Any],
    key: str,
    *,
    default: str | object = _MISSING,
    allow_empty: bool = False,
) -> str:
    value = payload.get(key, default)
    if value is _MISSING or value is None:
        raise RuntimeContractError(f"missing runtime request field: {key}")
    if not isinstance(value, str):
        raise RuntimeContractError(f"runtime request field {key} must be a string")
    if not allow_empty and not value.strip():
        raise RuntimeContractError(f"missing runtime request field: {key}")
    return value


def optional_string_field(
    payload: Mapping[str, Any],
    key: str,
    *,
    allow_empty: bool = False,
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RuntimeContractError(f"runtime request field {key} must be a string")
    if not allow_empty and not value.strip():
        raise RuntimeContractError(f"runtime request field {key} must be non-empty")
    return value


def string_list_field(payload: Mapping[str, Any], key: str) -> list[str]:
    values = list_field(payload, key)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise RuntimeContractError(
            f"runtime request field {key} must contain non-empty strings"
        )
    return [value for value in values if isinstance(value, str)]
