"""logging — structured JSON logging (M18, Phase 10 partial).

Server-side only. Every line is a JSON object: ts, level, logger, msg,
plus any extra context (e.g. review_id). Messages are secret-masked
before they hit the log (INV-3 — untrusted content never lands verbatim).
"""

from __future__ import annotations

import json
import logging
from collections.abc import MutableMapping
from datetime import datetime, timezone
from typing import Any

from backend.security.masking import mask_secrets


class JsonFormatter(logging.Formatter):
    """Format a LogRecord as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": mask_secrets(record.getMessage()),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str) -> logging.Logger:
    """Return a module logger wired to the JSON handler (idempotent)."""
    logger = logging.getLogger(name)
    if any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def with_context(
    logger: logging.Logger, **fields: Any
) -> logging.LoggerAdapter[Any]:
    """Wrap a logger so every call carries context (e.g. review_id).

        log = with_context(get_logger("webhook"), review_id=rid)
        log.info("claimed delivery")
    """
    return _ContextAdapter(logger, fields)


class _ContextAdapter(logging.LoggerAdapter[Any]):
    """Injects context fields into the JSON extra of every log call."""

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        extra = dict(kwargs.get("extra") or {})
        fields = dict(self.extra or {})
        fields.update(extra)
        kwargs["extra"] = {"extra_fields": fields}
        return msg, kwargs
