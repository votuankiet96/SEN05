"""Child-process environment sanitization."""

from __future__ import annotations

import os

_UNSAFE_SSLKEYLOG_PREFIXES = ("\\\\.\\", "\\\\??\\")
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _allow_ssl_keylogfile(env: os._Environ[str] | dict[str, str]) -> bool:
    return str(env.get("TICK_ENGINE_ALLOW_SSLKEYLOGFILE", "")).strip().lower() in _TRUE_VALUES


def _is_unsafe_ssl_keylog_target(value: str | None) -> bool:
    normalized = str(value or "").replace("/", "\\").strip()
    return bool(normalized) and normalized.startswith(_UNSAFE_SSLKEYLOG_PREFIXES)


def sanitize_ssl_keylogfile(env: os._Environ[str] | dict[str, str] | None = None) -> str | None:
    """Remove TLS keylog targets from an environment mapping unless opted in.

    ``SSLKEYLOGFILE`` is a debugging variable that asks TLS libraries to write
    session keys for packet analysis. It is not needed in production and can
    leak secrets. Some antivirus/network filters expose device paths such as
    ``\\.\aswMonFltProxy``; urllib3/truststore may try to open that value as a
    normal file and fail before a scheduled job can even start.

    Operators can explicitly keep it for diagnostics with
    ``TICK_ENGINE_ALLOW_SSLKEYLOGFILE=1``. Unsafe device paths are always
    removed regardless of the opt-in flag.
    """
    target = os.environ if env is None else env
    value = target.get("SSLKEYLOGFILE")
    if not value:
        return None
    if _allow_ssl_keylogfile(target) and not _is_unsafe_ssl_keylog_target(value):
        return None
    try:
        target.pop("SSLKEYLOGFILE", None)
        target["TICK_ENGINE_SSLKEYLOGFILE_IGNORED"] = str(value)
    except Exception:
        return None
    return str(value)


def _operator_env_values() -> set[str]:
    """Return the set of values held in sensitive operator env vars (for log scrubbing)."""
    sensitive = (
        "CTRADER_CLIENT_ID",
        "CTRADER_CLIENT_SECRET",
        "CTRADER_ACCESS_TOKEN",
        "CTRADER_REFRESH_TOKEN",
        "DISCORD_WEBHOOK_URL",
        "SQL_PWD",
    )
    return {v for k in sensitive if (v := os.environ.get(k, "")) and v}


def redact_operator_secrets(value: object) -> str:
    """Redact sensitive operator values from text before printing or logging."""
    text = str(value)
    for secret in sorted(_operator_env_values(), key=len, reverse=True):
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


def child_env(extra: dict | None = None) -> dict:
    """Build a sanitized environment dict for spawned subprocesses.

    Settings are already loaded into os.environ by settings.py at startup.
    Removes SSLKEYLOGFILE. Applies any extra overrides last.
    """
    env = dict(os.environ)
    sanitize_ssl_keylogfile(env)
    if extra:
        env.update(extra)
    return env
