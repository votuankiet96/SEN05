"""Build OG signals and store them in an output Redis DB (L_DES_* LIST/HASH). des does
NOT publish on Pub/Sub (decision 2026-10-25): the stored LIST/HASH is the only record.

des (the simulation copy of OG) NEVER expires or trims what it stores (decision
2026-10-07): no TTL on the HASH, no LTRIM on the LIST, and no expiration time
inside the signal. Every signal it publishes stays in Redis until someone
removes it on purpose -- or until the next start of the des process, which begins
from an empty signal DB (decision 2026-10-08, see clear_signals)."""

from __future__ import annotations

import json
import math
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
import redis


def as_utc(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _iso_utc(value: Any) -> str:
    return as_utc(value).isoformat().replace("+00:00", "Z")


def _required_number(row: Any, column: str) -> float:
    value = float(row[column])
    if not math.isfinite(value):
        raise ValueError(f"Signal field '{column}' must be a finite number")
    return round(value, 2)


def build_signal(
    *,
    strategy: str,
    timeframe: str,
    symbol: str,
    row: Any,
    valid_from: pd.Timestamp,
) -> tuple[str, str, dict[str, Any]]:
    """Build the internal (OG-domain) signal payload -- not Redis field names
    yet. ``to_hash_fields`` below is the one place that translates this into
    what actually gets written to the output HASH; keeping this function
    independent of that means Discord formatting (which reads this same
    dict) never has to know or care about cTrader's field names.

    Returns ``(signal_id, stamp, payload)``. `signal_id` duplicates
    strategy+timeframe+symbol+bartime on purpose: OF needs one ready-to-log,
    authoritative identifier for its own audit trail without re-deriving it
    from the key -- it becomes ``clientOrderId`` on the wire. `stamp` is the
    bartime rendered in the feed's own stamp format (``YYYY-MM-DD HH:MM:SS``),
    used to name the output HASH the same way DFS names its own candle HASHes.
    """
    strategy = strategy.lower()
    timeframe = timeframe.upper()
    symbol = symbol.upper()
    bartime = as_utc(row["bartime"])
    bartime_key = bartime.strftime("%Y%m%dT%H%M%SZ")
    signal_id = f"{strategy}:{timeframe}:{symbol}:{bartime_key}"
    stamp = bartime.strftime("%Y-%m-%d %H:%M:%S")

    signal = int(row["signal"])
    if signal not in (-1, 1):
        raise ValueError("Only BUY/SELL rows can be published")

    payload = {
        "strategy": strategy,
        "timeframe": timeframe,
        "symbol": symbol,
        "side": "BUY" if signal == 1 else "SELL",
        "bartime": _iso_utc(bartime),
        "signal_id": signal_id,
        "entry": _required_number(row, "entry_price"),
        "atr": _required_number(row, "atr"),
        "valid_from": _iso_utc(valid_from),
        "reason": str(row.get("signal_reason", "")),
    }
    # SL/TP không có ở đây: OF tự tính từ bảng KSL/KTP của họ (2026-10-04).
    return signal_id, stamp, payload


# combo's entry is a breakout level at the edge of the signal bar (BUY = its high,
# SELL = its low, no buffer since 2026-10-21) -- a pending STOP order. ma_cross's and macd_break's entry is the bar's own close,
# acted on right away -- read as "act now", a MARKET order. Fixed per strategy, not an operator knob (it is baked into
# each strategy's own entry formula, see CLAUDE.md mục 6).
_ORDER_TYPE_BY_STRATEGY = {"combo": "STOP", "ma_cross": "MARKET", "macd_break": "MARKET"}


def to_hash_fields(strategy: str, payload: dict[str, Any]) -> dict[str, str]:
    """Translate the internal payload into output HASH fields for OF.

    Three groups (field-by-field mapping to OF / cTrader):
      1. Identity, lower_snake, redundant with the key on purpose (same
         reason DFS's own HASH repeats `timestamp`) -- symbol/timeframe/
         strategy/bartime, so OF never has to parse a key name.
      2. Order-placement fields, camelCase, aligned 1:1 with
         ``ProtoOANewOrderReq`` field names where a direct match exists --
         tradeSide/orderType/stopPrice/clientOrderId/comment. des sets NO
         `expirationTimestamp` (no expiry, decision 2026-10-07).
         `orderType`'s NAME matches cTrader but its value here is still a
         plain string ("STOP"/"MARKET") -- OF must still map that to the
         protobuf enum, name alignment alone cannot remove that step.
         `symbol` here is OG's plain string, never cTrader's numeric
         `symbolId` -- that mapping is entirely OF's to do.
      3. OF-support fields, not part of cTrader's API at all -- atr (OF's
         own volume/position-sizing input and SL/TP distance base: OF
         computes SL/TP itself from its own KSL/KTP table times this atr),
         valid_from (the bar's CLOSE time, the earliest moment the signal can
         act -- informational here, des applies no expiry).
    """
    strategy = strategy.lower()
    order_type = _ORDER_TYPE_BY_STRATEGY[strategy]
    fields: dict[str, str] = {
        "symbol": str(payload["symbol"]),
        "timeframe": str(payload["timeframe"]),
        "strategy": strategy,
        "bartime": str(payload["bartime"]),
        "tradeSide": str(payload["side"]),
        "orderType": order_type,
        "clientOrderId": str(payload["signal_id"]),
        "comment": str(payload.get("reason", "")),
        "atr": str(payload["atr"]),
        "valid_from": str(payload["valid_from"]),
    }
    if order_type == "STOP":
        fields["stopPrice"] = str(payload["entry"])
    return fields


def strategy_token(strategy: str) -> str:
    """The STRATEGY token used in Redis key names: upper-case with
    no underscore, so ``ma_cross`` becomes ``MACROSS``, ``macd_break`` becomes
    ``MACDBREAK`` and no token ever
    contains the ``_`` delimiter (chốt 2026-10-05). Names only -- the
    ``strategy`` field and ``clientOrderId`` inside the HASH/message keep
    the strategy's real name (``ma_cross``).
    """
    return strategy.upper().replace("_", "")


def og_list_key(key_prefix: str, strategy: str, symbol: str, timeframe: str) -> str:
    """The OG LIST of stamps for one (strategy, symbol, timeframe), oldest
    first -- ``{key_prefix}_{STRATEGY}_{SYMBOL}_{TIMEFRAME}``, e.g.
    ``L_DES_COMBO_US30_H4`` / ``L_DES_MACROSS_US30_M30``. Underscore join, zero
    colons, STRATEGY first (chốt 2026-10-04).
    """
    return f"{key_prefix}_{strategy_token(strategy)}_{symbol.upper()}_{timeframe.upper()}"


def stamp_hash_key(list_key: str, stamp: str) -> str:
    """The HASH holding one signal: the list's key plus ``":" + stamp``,
    the exact same one-concatenation rule DFS uses for its own candle HASHes.
    """
    return f"{list_key}:{stamp}"


# KEYS[1]=og_hash KEYS[2]=og_list
# ARGV[1]=stamp ARGV[2..]=field1 value1 field2 value2 ...
#
# "Already published?" is answered by the OG HASH existing -- a duplicate
# (retried event, reconcile re-scan) writes nothing. Otherwise, in one atomic
# round trip: the OG HASH and LIST are written (NO EXPIRE, NO LTRIM --
# everything is kept, see the module docstring). Nothing is published on Pub/Sub.
_PUBLISH_SIGNAL_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return 0
end
redis.call('HSET', KEYS[1], unpack(ARGV, 2))
redis.call('RPUSH', KEYS[2], ARGV[1])
return 1
"""


def publish_signal(
    client: redis.Redis,
    *,
    key_prefix: str,
    symbol: str,
    timeframe: str,
    strategy: str,
    stamp: str,
    hash_fields: dict[str, str],
) -> bool:
    """Atomically write once: OG HASH + LIST entry -- all skipped if this exact signal
    already exists in this output DB. One EVAL round-trip. Returns True only when it
    actually wrote.
    """
    og_list = og_list_key(key_prefix, strategy, symbol, timeframe)
    args: list[str] = [stamp]
    for field, value in hash_fields.items():
        args.append(field)
        args.append(value)
    created = client.eval(
        _PUBLISH_SIGNAL_SCRIPT,
        2,
        stamp_hash_key(og_list, stamp),
        og_list,
        *args,
    )
    return bool(int(created))


_CLEAR_BATCH = 500


def clear_signals(client: redis.Redis, *, key_prefix: str) -> int:
    """Delete every des signal key (``{key_prefix}_*``: the LISTs and their HASHes)
    from the client's DB and return how many keys went.

    Each start of the des process begins from an empty signal DB: a signal that
    survived from an earlier run would make the "HASH already exists" check swallow
    the same signal when the replay reaches it again (no write)
    and would leave the LISTs out of order. Only the ``{key_prefix}_`` namespace is
    touched -- never a FLUSHDB.
    """
    deleted = 0
    batch: list[str] = []
    for key in client.scan_iter(match=f"{key_prefix}_*", count=1000):
        batch.append(key)
        if len(batch) >= _CLEAR_BATCH:
            deleted += int(client.unlink(*batch))
            batch = []
    if batch:
        deleted += int(client.unlink(*batch))
    return deleted


def _display_number(value: Any) -> str:
    return f"{float(value):.10f}".rstrip("0").rstrip(".")


def format_discord_message(payload: dict[str, Any]) -> str:
    """Format one Redis signal payload as a compact Discord message."""
    strategy = str(payload["strategy"]).replace("_", " ").title()
    lines = [
        f"**{payload['side']} | {strategy} | {payload['symbol']} {payload['timeframe']}**",
        f"Bar: `{payload['bartime']}`",
        f"Entry: `{_display_number(payload['entry'])}`",
    ]
    lines.extend(
        [
            f"ATR: `{_display_number(payload['atr'])}`",
            f"Valid from: `{payload['valid_from']}`",
        ]
    )
    reason = str(payload.get("reason") or "").strip()
    if reason:
        lines.extend(["", reason])
    return "\n".join(lines)[:2000]


def _post_discord(url: str, body: dict[str, Any], timeout_seconds: float) -> int:
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "OG-Signal/1"},
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return int(response.status)


def send_discord_signal(
    payload: dict[str, Any],
    discord_config: dict[str, Any],
) -> tuple[bool, str]:
    """Send one best-effort Discord alert with a short configured retry."""
    webhook_url = str(discord_config["webhook_url"]).strip()
    if not webhook_url:
        return False, "missing webhook_url"

    body = {"content": format_discord_message(payload)}
    username = str(discord_config["username"]).strip()
    if username:
        body["username"] = username

    retry_count = int(discord_config["retry_count"])
    retry_delay = float(discord_config["retry_delay_seconds"])
    timeout = float(discord_config["timeout_seconds"])
    detail = "not sent"

    for attempt in range(retry_count + 1):
        retryable = True
        try:
            status = _post_discord(webhook_url, body, timeout)
            if status in {200, 204}:
                return True, "sent"
            detail = f"HTTP {status}"
            retryable = status == 429 or status >= 500
        except HTTPError as exc:
            detail = f"HTTP {exc.code}"
            retryable = exc.code == 429 or exc.code >= 500
        except (URLError, TimeoutError, OSError) as exc:
            detail = f"network error: {exc}"

        if attempt >= retry_count or not retryable:
            break
        time.sleep(retry_delay)
    return False, detail
