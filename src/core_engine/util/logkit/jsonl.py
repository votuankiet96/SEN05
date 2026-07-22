"""Size-capped JSONL append helper.

Round-2 audit finding (Codex): live_fetching_summary.jsonl and
historical_pulling_summary.jsonl were appended to forever via a plain
open(path, "a") with no cap at all, and were NOT protected by
health.cleanup_old_runtime_files' age-based retention either - that
retention checks each file's mtime against a cutoff, but an actively-
appended file's mtime is refreshed on every write, so it never looks old
enough to delete no matter how large it grows or how old its oldest
records are.

append_jsonl_capped appends normally, then - only when the file has grown
past max_bytes - rewrites it keeping just the last keep_lines lines. This
keeps the file bounded without an age-based scheme, which does not fit an
always-being-written-to file anyway. Callers only ever read the LAST line
of these files (see health._read_last_jsonl), so trimming old lines does
not lose anything anyone reads.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_KEEP_LINES = 5000


def append_jsonl_capped(
    path: Path | str,
    row: dict[str, Any],
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    keep_lines: int = DEFAULT_KEEP_LINES,
    strict: bool = False,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")

    try:
        if max_bytes > 0 and path.stat().st_size >= max_bytes:
            _truncate_to_tail(path, keep_lines)
    except OSError:
        if strict:
            raise


def _truncate_to_tail(path: Path, keep_lines: int) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) <= keep_lines:
        return
    tail = lines[-keep_lines:]
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text("\n".join(tail) + "\n", encoding="utf-8")
    tmp.replace(path)
