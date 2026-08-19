"""Dispatch configuration — phase-to-agent mapping with smart defaults."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


TIER_MODELS: dict[str, dict[str, str]] = {
    "claude": {
        "strong": "claude-opus-4-8",
        "fast": "claude-sonnet-5",
        "default": "claude-sonnet-5",
        "cheap": "claude-haiku-4-5",
    },
    "codex": {
        "strong": "o3",
        "fast": "o4-mini",
        "default": "o4-mini",
        "cheap": "gpt-4.1-nano",
    },
    "kiro": {
        "strong": "claude-opus-4-8",
        "fast": "claude-sonnet-5",
        "default": "claude-sonnet-5",
        "cheap": "claude-haiku-4-5",
    },
}

DEFAULT_DISPATCH: dict[str, dict[str, str]] = {
    "inception": {"agent": "claude", "tier": "strong"},
    "construction": {"agent": "claude", "tier": "fast"},
    "validation": {"agent": "claude", "tier": "strong"},
    "release": {"agent": "claude", "tier": "default"},
    "operations": {"agent": "claude", "tier": "default"},
}

ESCALATION_DEFAULTS: dict[str, Any] = {
    "consecutive_failures": 2,
    "tier_escalation": "strong",
}


def resolve_model(agent: str, tier_or_model: str) -> str:
    agent_tiers = TIER_MODELS.get(agent, {})
    return agent_tiers.get(tier_or_model, tier_or_model)


def load_dispatch_config(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    config_path = root / ".isekai" / "dispatch.json"
    config: dict[str, Any] = {}
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    phase_dispatch = config.get("phase_dispatch", {})
    merged: dict[str, dict[str, str]] = {}
    for phase_id, defaults in DEFAULT_DISPATCH.items():
        override = phase_dispatch.get(phase_id, {})
        agent = override.get("agent", defaults["agent"])
        tier_key = "tier" if "tier" in defaults else "model"
        model_or_tier = override.get("model", override.get("tier", defaults.get(tier_key, defaults.get("model", "default"))))
        merged[phase_id] = {
            "agent": agent,
            "model": resolve_model(agent, model_or_tier),
        }
    escalation = config.get("escalation", ESCALATION_DEFAULTS)
    return {
        "default_agent": config.get("default_agent", "claude"),
        "phase_dispatch": merged,
        "escalation": escalation,
    }
