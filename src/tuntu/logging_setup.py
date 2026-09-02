from __future__ import annotations

import logging
import os
import re
from logging.handlers import TimedRotatingFileHandler


_SENSITIVE_KEY = r"authorization|cookie|password|cd2_api_token|api_token"
_DOUBLE_QUOTED_SECRET = re.compile(
    rf'''(?i)(["']?(?:{_SENSITIVE_KEY})["']?\s*[:=]\s*)"(?:\\.|[^"\\])*"'''
)
_SINGLE_QUOTED_SECRET = re.compile(
    rf"(?i)([\"']?(?:{_SENSITIVE_KEY})[\"']?\s*[:=]\s*)'(?:\\.|[^'\\])*'"
)
_AUTHORIZATION = re.compile(
    r"(?i)(authorization)(\s*[:=]\s*)((?:bearer\s+)?[^\s,;]+)"
)
_COOKIE = re.compile(
    r"(?i)(cookie)(\s*[:=]\s*)([^\r\n]+?)"
    r"(?=\s+(?:authorization|password|cd2_api_token|api_token)\s*[:=]|$)"
)
_SECRET_FIELD = re.compile(
    r"(?i)(password|cd2_api_token|api_token)(\s*[:=]\s*)([^\s,;]+)"
)


def redact_text(value: str) -> str:
    value = _DOUBLE_QUOTED_SECRET.sub(
        lambda match: match.group(1) + '"<redacted>"', value
    )
    value = _SINGLE_QUOTED_SECRET.sub(
        lambda match: match.group(1) + "'<redacted>'", value
    )
    for pattern in (_AUTHORIZATION, _COOKIE, _SECRET_FIELD):
        value = pattern.sub(
            lambda match: match.group(1) + match.group(2) + "<redacted>",
            value,
        )
    return value


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        record.msg = redact_text(rendered)
        record.args = ()
        return True


def configure_logging(config) -> None:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(config.log_dir, 0o700)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    redaction = SecretRedactionFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(redaction)

    log_path = config.log_dir / "tuntu.log"
    file_handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=True,
    )
    os.chmod(log_path, 0o600)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redaction)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))
    root.addHandler(console)
    root.addHandler(file_handler)
