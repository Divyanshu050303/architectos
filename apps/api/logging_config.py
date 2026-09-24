"""Structured (JSON) logging for the API process.

One JSON object per line with the message, level, logger, timestamp, request id and any
``extra`` fields. Values that could carry secrets are never passed to loggers in this codebase;
uvicorn's own access log is disabled because it prints raw URL paths, which can contain tokens
(e.g. /invitations/{token}/accept). apps.api.middleware.logging logs route templates instead.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from apps.api.middleware.request_id import current_request_id

_STANDARD = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or current_request_id()
        if request_id:
            payload["request_id"] = request_id
        payload.update({key: value for key, value in record.__dict__.items() if key not in _STANDARD})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True
