from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|bearer|credential|password|secret|signed[_-]?url|token)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_QUERY_SECRET = re.compile(r"(?i)([?&](?:token|key|signature|sig|auth|credential)=)[^&\s]+")


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    # Generation telemetry is safe and intentionally visible in diagnostics.
    if normalized.endswith("_tokens") or normalized in {"token_count", "tokens_used"}:
        return False
    return bool(_SENSITIVE_KEY.search(normalized))


def redact_diagnostics(value: Any) -> Any:
    """Recursively remove credentials before diagnostics are persisted or copied."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _is_sensitive_key(key) else redact_diagnostics(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_diagnostics(item) for item in value]
    if isinstance(value, tuple):
        return [redact_diagnostics(item) for item in value]
    if isinstance(value, str):
        return _QUERY_SECRET.sub(r"\1[REDACTED]", _BEARER.sub("Bearer [REDACTED]", value))
    return value
