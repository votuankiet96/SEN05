"""Application-wide TLS bootstrap for Windows-managed certificate stores."""

from __future__ import annotations

import threading


_lock = threading.Lock()
_active: bool | None = None


def ensure_system_truststore() -> bool:
    """Inject the OS trust store once, before HTTP clients create sessions.

    Production traffic can pass through a host-managed TLS inspection proxy
    whose root is trusted by Windows but absent from certifi.  Network modules
    must not depend on TradingView auth having been imported first merely to
    activate that trust chain.
    """
    global _active
    if _active is not None:
        return _active
    with _lock:
        if _active is not None:
            return _active
        try:
            import truststore  # type: ignore

            truststore.inject_into_ssl()
            _active = True
        except Exception:
            _active = False
        return _active
