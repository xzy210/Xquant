from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from typing import Any

SENSITIVE_FIELD_TOKENS = (
    "token",
    "api_key",
    "apikey",
    "secret",
    "password",
    "passwd",
    "credential",
    "access_key",
    "private_key",
    "account_id",
    "broker_account",
)

MASK = "***REDACTED***"


def redact_sensitive(value: Any) -> Any:
    """Return a JSON-safe copy with sensitive fields masked."""

    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                redacted[key] = _redact_scalar(item)
            else:
                redacted[key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    if isinstance(value, set):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(token in normalized for token in SENSITIVE_FIELD_TOKENS)


def _redact_scalar(value: Any) -> Any:
    if value in (None, "", False):
        return value
    return MASK


def _redact_text(value: str) -> str:
    text = str(value or "")
    patterns = [
        r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,;，；]+)",
        r"(?i)(token\s*[:=]\s*)([^\s,;，；]+)",
        r"(?i)(secret\s*[:=]\s*)([^\s,;，；]+)",
        r"(?i)(password\s*[:=]\s*)([^\s,;，；]+)",
        r"(?i)(account[_-]?id\s*[:=]\s*)([^\s,;，；]+)",
        r"(账户ID\s*[:：]\s*)([^\s,;，；]+)",
    ]
    for pattern in patterns:
        text = re.sub(pattern, rf"\1{MASK}", text)
    return text


__all__ = ["MASK", "SENSITIVE_FIELD_TOKENS", "redact_sensitive"]
