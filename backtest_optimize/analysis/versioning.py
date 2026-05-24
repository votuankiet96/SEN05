"""Version snapshot utilities."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd

SNAPSHOT_ROOT = Path(__file__).resolve().parents[1] / "outputs" / "version_snapshots"


def file_md5(path: str | Path) -> str:
    """Return MD5 hash for a file."""
    file_path = Path(path)
    digest = hashlib.md5()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    """Return MD5 hash of a JSON-stable value."""
    payload = json.dumps(to_jsonable(value), sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def git_commit_hash(cwd: str | Path | None = None) -> str | None:
    """Return current git commit hash when available."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd) if cwd is not None else None,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def to_jsonable(value: Any) -> Any:
    """Convert common project objects to JSON-compatible values."""
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    return value


def save_snapshot(
    *,
    config: dict[str, Any],
    result_summary: dict[str, Any],
    signal_file: str | Path | None = None,
    market_data_source_id: str | None = None,
    assumptions: Any = None,
    output_dir: str | Path = SNAPSHOT_ROOT,
    name: str | None = None,
    repo_root: str | Path | None = None,
) -> Path:
    """Save a diff-friendly JSON run snapshot."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    signal_path = Path(signal_file) if signal_file is not None else None
    payload = {
        "created_at": datetime.utcnow().isoformat(),
        "name": name,
        "git_commit": git_commit_hash(repo_root),
        "signal_file": str(signal_path) if signal_path else None,
        "signal_file_md5": file_md5(signal_path) if signal_path and signal_path.exists() else None,
        "market_data_source_id": market_data_source_id,
        "config": to_jsonable(config),
        "config_hash": stable_hash(config),
        "assumptions": to_jsonable(assumptions),
        "result_summary": to_jsonable(result_summary),
    }
    payload["snapshot_hash"] = stable_hash(payload)

    stem = name or f"snapshot_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    file_path = output_path / f"{stem}.json"
    file_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True),
        encoding="utf-8",
    )
    return file_path
