from __future__ import annotations

import json
import logging
import time
from typing import Any


LOGGER = logging.getLogger("isekai")

_JSON_HANDLER_ATTR = "_isekai_json_handler"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for attr in ("action", "duration_ms", "error", "unit", "project"):
            value = getattr(record, attr, None)
            if value is not None:
                entry[attr] = value
        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = str(record.exc_info[1])
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(*, level: int = logging.INFO, force: bool = False) -> None:
    if not force and getattr(LOGGER, _JSON_HANDLER_ATTR, False):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    LOGGER.handlers = [handler]
    LOGGER.setLevel(level)
    LOGGER.propagate = False
    setattr(LOGGER, _JSON_HANDLER_ATTR, True)


class ActionTimer:
    __slots__ = ("_action", "_start", "_extra")

    def __init__(self, action: str, **extra: Any) -> None:
        self._action = action
        self._start = time.monotonic()
        self._extra = extra

    def ok(self, msg: str = "completed", **extra: Any) -> None:
        elapsed = (time.monotonic() - self._start) * 1000
        LOGGER.info(
            msg,
            extra={"action": self._action, "duration_ms": round(elapsed, 1), **self._extra, **extra},
        )

    def fail(self, error: str, **extra: Any) -> None:
        elapsed = (time.monotonic() - self._start) * 1000
        LOGGER.warning(
            "failed",
            extra={"action": self._action, "duration_ms": round(elapsed, 1), "error": error, **self._extra, **extra},
        )
