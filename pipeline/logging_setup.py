"""Structured (JSON-lines) logging.

Every log line is a single JSON object so a reviewer can grep / pipe the run
without reading source. ``extra={"event": "...", ...}`` fields are merged into
the record, so stage code can attach structured context cheaply.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

# Standard LogRecord attributes we don't want to duplicate into the JSON body.
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message", "asctime", "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Merge structured extras (anything not a standard LogRecord field).
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure root logging to emit JSON lines to stdout. Idempotent."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    # Replace any existing handlers so repeated calls don't double-log.
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    # httpx logs every request at INFO — quiet it to WARNING to keep runs readable.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logging.getLogger("pipeline")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"pipeline.{name}")
