"""Compatibility façade for request intake and normalization."""

from .workflow.intake import (
    CHANGE_VALUES,
    INTAKE_SOURCES,
    RISK_VALUES,
    _infer_change,
    _matches,
    _strings,
    _text,
    intake,
    normalize_intent,
)

__all__ = ["intake", "normalize_intent"]
