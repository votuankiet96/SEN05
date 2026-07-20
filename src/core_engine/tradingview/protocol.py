"""TradingView WebSocket wire-format helpers for the live feed."""

from __future__ import annotations

import ast
import json
import random
import re
import string
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import pandas as pd


def gen_session_id(prefix: str) -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"{prefix}_{suffix}"


def send_tv_message(ws: Any, msg: list) -> None:
    method = msg[0]
    params = list(msg[1:])
    payload = json.dumps({"m": method, "p": params})
    ws.send(f"~m~{len(payload)}~m~{payload}")


def parse_packets(raw: str) -> list[str]:
    packets: list[str] = []
    pos = 0
    while pos < len(raw):
        if raw[pos : pos + 3] != "~m~":
            break
        pos += 3
        sep = raw.find("~m~", pos)
        if sep == -1:
            break
        length_str = raw[pos:sep]
        pos = sep + 3
        try:
            length = int(length_str)
        except ValueError:
            break
        packets.append(raw[pos : pos + length])
        pos += length
    return packets


def bars_to_df(bars: list) -> pd.DataFrame:
    records = []
    for bar in bars:
        v = bar.get("v", [])
        if len(v) < 6:
            continue
        ts = datetime.fromtimestamp(v[0], tz=timezone.utc).replace(tzinfo=None)
        records.append(
            {
                "__ts__": ts,
                "open": float(v[1]),
                "high": float(v[2]),
                "low": float(v[3]),
                "close": float(v[4]),
                "volume": float(v[5]) if v[5] == v[5] else None,
            }
        )
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records).set_index("__ts__")
    df.index.name = None
    return df


def headers_get(headers: Any, name: str) -> str | None:
    if not headers:
        return None
    target = name.lower()
    if hasattr(headers, "items"):
        try:
            for key, value in headers.items():
                if str(key).lower() == target:
                    return str(value)
        except Exception:
            return None
    if isinstance(headers, (list, tuple)):
        for item in headers:
            raw = str(item)
            if ":" not in raw:
                continue
            key, _, value = raw.partition(":")
            if key.strip().lower() == target:
                return value.strip()
    return None


def retry_after_seconds(headers: Any, default_seconds: int, *, max_seconds: int) -> int:
    raw = headers_get(headers, "Retry-After")
    if not raw:
        return default_seconds
    try:
        return max(1, min(int(float(raw)), max_seconds))
    except (TypeError, ValueError):
        pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        wait = int((dt.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
        return max(1, min(wait, max_seconds))
    except Exception:
        return default_seconds


def extract_ws_status(error: Any) -> tuple[int | None, Any]:
    status = getattr(error, "status_code", None)
    headers = getattr(error, "resp_headers", None)
    if status is None:
        status = getattr(error, "status", None)
    if status is None:
        match = re.search(
            r"(?:Handshake status|HTTP|status(?:_code)?=?)\s*[:=]?\s*(\d{3})",
            str(error),
        )
        if match:
            status = match.group(1)
    try:
        return (int(status) if status is not None else None), headers
    except (TypeError, ValueError):
        return None, headers


def extract_ws_close_code(error: Any) -> int | None:
    """Return a WebSocket close code without confusing it with an HTTP status."""
    for attr in ("close_status_code", "close_code", "code"):
        value = getattr(error, attr, None)
        try:
            code = int(value)
        except (TypeError, ValueError):
            continue
        if 1000 <= code <= 4999:
            return code

    text = str(error)
    if "opcode=8" not in text.lower() and "close frame" not in text.lower():
        return None

    match = re.search(r"data=(b(?:'[^']*'|\"[^\"]*\"))", text, flags=re.IGNORECASE)
    if match:
        try:
            payload = ast.literal_eval(match.group(1))
        except (SyntaxError, ValueError):
            payload = None
        if isinstance(payload, bytes) and len(payload) >= 2:
            return int.from_bytes(payload[:2], byteorder="big", signed=False)

    match = re.search(r"(?:close(?:\s+status)?(?:\s+code)?|code)\s*[:=]\s*(\d{4})", text, re.IGNORECASE)
    if match:
        code = int(match.group(1))
        if 1000 <= code <= 4999:
            return code
    return None


def classify_ws_error(
    error: Any,
    *,
    reconnect_max_sec: int,
    rate_limit_cooldown_sec: int,
    forbidden_cooldown_sec: int,
    token_expiry_keywords: tuple[str, ...],
) -> tuple[str, int | None, int]:
    close_code = extract_ws_close_code(error)
    if close_code == 1000:
        return "normal_close", close_code, 0

    status, headers = extract_ws_status(error)
    text = str(error).lower()
    if status == 429:
        return (
            "rate_limit",
            status,
            retry_after_seconds(headers, rate_limit_cooldown_sec, max_seconds=reconnect_max_sec),
        )
    if status in (401, 403):
        cooldown = forbidden_cooldown_sec if status == 403 else 0
        return "auth", status, cooldown
    if status is not None and status >= 500:
        return "server", status, 0
    if any(keyword in text for keyword in token_expiry_keywords):
        return "auth", status, 0
    if "too many" in text or "rate limit" in text:
        return "rate_limit", status, rate_limit_cooldown_sec
    return "network", status, 0
