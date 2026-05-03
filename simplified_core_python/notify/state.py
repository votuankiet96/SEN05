"""Small JSON state store used to prevent duplicate signal alerts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_STATE_PATH = Path(__file__).resolve().parent / "state.json"


class SignalState:
    """Persist sent signal keys in a local JSON file."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_STATE_PATH
        self.sent: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self.sent = set()
            return
        try:
            data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.sent = set()
            return
        self.sent = {str(item) for item in data.get("sent", [])}

    def has(self, key: str) -> bool:
        """Return True if key was already recorded."""
        return key in self.sent

    def add(self, key: str) -> None:
        """Record a sent key and persist state atomically."""
        self.sent.add(key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"sent": sorted(self.sent)}
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def signal_key(strategy: str, symbol: str, tf: str, bartime: object, signal: int) -> str:
    """Build a stable duplicate-prevention key for a signal row."""
    return f"{strategy}|{symbol.upper()}|{tf.upper()}|{bartime}|{int(signal)}"

