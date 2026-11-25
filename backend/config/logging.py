"""Small JSON log formatter for safe, parseable operational messages."""
import json
import logging
import re
from datetime import datetime, timezone


_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|token|authorization|api[_-]?key)\b(\s*[:=]\s*)([^\s,;]+(?:\s+[^\s,;]+)?)"
)


class JsonLogFormatter(logging.Formatter):
    def format(self, record):
        message = record.getMessage()
        message = _CREDENTIAL_PATTERN.sub(r"\1\2[REDACTED]", message)
        return json.dumps({
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }, ensure_ascii=True, separators=(",", ":"))
