"""Build and publish complete OG signals to Redis DB1."""

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


def _optional_number(row: Any, column: str, *, digits: int = 2) -> float | None:
    try:
        value = float(row[column])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return round(value, digits)


def build_signal(
    *,
    strategy: str,
    timeframe: str,
    symbol: str,
    row: Any,
    valid_from: pd.Timestamp,
    valid_until: pd.Timestamp,
) -> tuple[str, str, dict[str, Any]]:
    """Build the internal (OG-domain) signal payload -- not Redis field names
    yet. ``to_hash_fields`` below is the one place that translates this into
    what actually gets written to the DB1 HASH; keeping this function
    independent of that means Discord formatting (which reads this same
    dict) never has to know or care about cTrader's field names.

    Returns ``(signal_id, stamp, payload)``. `signal_id` duplicates
    strategy+timeframe+symbol+bartime on purpose: OF needs one ready-to-log,
    authoritative identifier for its own audit trail without re-deriving it
    from the key -- it becomes ``clientOrderId`` on the wire. `stamp` is the
    bartime rendered in DP's own stamp format (``YYYY-MM-DD HH:MM:SS``), used
    to name the DB1 HASH the same way DP names its own DB0 HASHes.
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
        "valid_until": _iso_utc(valid_until),
        "reason": str(row.get("signal_reason", "")),
    }
    # sl_dow chỉ có ở ma_cross (levels.py: include_sl_dow=True), combo không
    # có cột này nên _optional_number trả None và field bị bỏ qua.
    optional_fields = {
        "sl": "sl_price",
        "tp": "tp_price",
        "sl_dow": "sl_dow",
    }
    for payload_key, row_key in optional_fields.items():
        value = _optional_number(row, row_key)
        if value is not None:
            payload[payload_key] = value

    # ksl/ktp are exact 3-decimal ratios from a fixed set (levels.py:
    # KSL_LEVELS/KTP_LEVELS), not prices -- round(., 2) above would corrupt
    # them (e.g. 2.058 -> 2.06).
    for payload_key in ("ksl", "ktp"):
        value = _optional_number(row, payload_key, digits=3)
        if value is not None:
            payload[payload_key] = value
    return signal_id, stamp, payload


# combo's entry is a breakout level outside the current price (high+X /
# low-X) -- a pending STOP order. ma_cross's entry is the bar's own close,
# acted on within a very short validity window -- read as "act now", a
# MARKET order. Fixed per strategy, not an operator knob (it is baked into
# each strategy's own entry formula, see CLAUDE.md mục 6).
_ORDER_TYPE_BY_STRATEGY = {"combo": "STOP", "ma_cross": "MARKET"}


def to_hash_fields(strategy: str, payload: dict[str, Any]) -> dict[str, str]:
    """Translate the internal payload into DB1 HASH fields for OF.

    Three groups (see CLAUDE.md mục 7 / memory project_of_ctrader_integration
    for the full field-by-field cTrader audit):
      1. Identity, lower_snake, redundant with the key on purpose (same
         reason DP's own HASH repeats `timestamp`) -- symbol/timeframe/
         strategy/bartime, so OF never has to parse a key name.
      2. Order-placement fields, camelCase, aligned 1:1 with
         ``ProtoOANewOrderReq`` field names where a direct match exists --
         tradeSide/orderType/stopPrice/stopLoss/takeProfit/
         expirationTimestamp/clientOrderId/comment. `expirationTimestamp` is
         epoch seconds (not ISO) to match cTrader's int64 type exactly.
         `orderType`'s NAME matches cTrader but its value here is still a
         plain string ("STOP"/"MARKET") -- OF must still map that to the
         protobuf enum, name alignment alone cannot remove that step.
         `symbol` here is OG's plain string, never cTrader's numeric
         `symbolId` -- that mapping is entirely OF's to do.
      3. OF-support fields, not part of cTrader's API at all -- atr (OF's
         own volume/position-sizing input), valid_from (OF's own freshness
         gate before it even calls the API), ksl/ktp (the KSL/KTP ratio
         behind stopLoss/takeProfit, e.g. 1.618 -- lets OF derive its own
         relative stop-loss/take-profit distance as ksl*atr/ktp*atr instead
         of trusting only the absolute stopLoss/takeProfit computed against
         OG's own entry price, which matters for ma_cross's MARKET fill
         price differing from OG's signal-time close).
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
        "expirationTimestamp": str(int(as_utc(payload["valid_until"]).timestamp())),
        "clientOrderId": str(payload["signal_id"]),
        "comment": str(payload.get("reason", "")),
        "atr": str(payload["atr"]),
        "valid_from": str(payload["valid_from"]),
    }
    if order_type == "STOP":
        fields["stopPrice"] = str(payload["entry"])
    if "sl" in payload:
        fields["stopLoss"] = str(payload["sl"])
    if "tp" in payload:
        fields["takeProfit"] = str(payload["tp"])
    if "ksl" in payload:
        fields["ksl"] = str(payload["ksl"])
    if "ktp" in payload:
        fields["ktp"] = str(payload["ktp"])
    return fields


def signal_list_key(key_prefix: str, symbol: str, timeframe: str, strategy: str) -> str:
    """The LIST of stamps for one (symbol, timeframe, strategy) -- the
    triple's only index, oldest first. Mirrors DP's own DB0 shape
    (candle_reader.py:pair_list_key) -- underscore join, zero
    colons, SYMBOL before TIMEFRAME -- with STRATEGY appended, since unlike
    a candle a signal is always tied to exactly one strategy.
    """
    return f"{key_prefix}_{symbol.upper()}_{timeframe.upper()}_{strategy.upper()}"


def signal_hash_key(list_key: str, stamp: str) -> str:
    """The HASH holding one signal: the list's key plus ``":" + stamp``,
    the exact same one-concatenation rule DP uses for DB0's own HASHes.
    """
    return f"{list_key}:{stamp}"


# KEYS[1]=hash_key KEYS[2]=list_key
# ARGV[1]=stamp ARGV[2]=retention_seconds ARGV[3]=max_list_entries
# ARGV[4]=event_channel ARGV[5]=event_message
# ARGV[6..]=field1 value1 field2 value2 ...
#
# "Already published?" is answered by HASH existence -- if the hash is
# already there this is a duplicate (retried event, reconcile re-scan) and
# nothing is written, the same exactly-once guarantee the old `SET NX`
# design had, just spanning 2 keys instead of 1 in a single round trip.
# EXPIRE goes on the HASH specifically (each signal keeps its own
# retention_seconds TTL, exactly like the old SET EX did) -- the LIST has
# no such per-element TTL, so it is bounded by LTRIM to the newest
# max_list_entries instead.
#
# PUBLISH (added 2026-09-18) sits inside the same "actually wrote" branch,
# after the writes it announces -- a duplicate attempt returns 0 before
# reaching it, so OF is never notified twice for the same signal. Same
# design as DP's own dp:events:candles toward OG (see event_listener.py):
# the message is a trigger only, OF re-reads the HASH itself rather than
# trusting embedded fields (see CLAUDE.md muc 7 integrity checklist).
_PUBLISH_SIGNAL_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return 0
end
redis.call('HSET', KEYS[1], unpack(ARGV, 6))
redis.call('EXPIRE', KEYS[1], ARGV[2])
redis.call('RPUSH', KEYS[2], ARGV[1])
redis.call('LTRIM', KEYS[2], -tonumber(ARGV[3]), -1)
redis.call('PUBLISH', ARGV[4], ARGV[5])
return 1
"""


def signal_event_message(symbol: str, timeframe: str, strategy: str, stamp: str) -> str:
    """Build the trigger-only Pub/Sub message announcing one new DB1 signal.

    Mirrors DP's own ``{"symbol":..., "timeframe":...}`` shape toward OG
    (see event_listener.py) -- enough for OF to rebuild the exact HASH key
    itself (``signal_list_key`` + ``signal_hash_key``) and re-``HGETALL``
    it, never enough to act on the message's own fields directly.
    """
    return json.dumps(
        {
            "symbol": symbol.upper(),
            "timeframe": timeframe.upper(),
            "strategy": strategy.lower(),
            "stamp": stamp,
        }
    )


def publish_signal(
    client: redis.Redis,
    *,
    key_prefix: str,
    symbol: str,
    timeframe: str,
    strategy: str,
    stamp: str,
    hash_fields: dict[str, str],
    retention_seconds: int,
    max_list_entries: int,
    event_channel: str,
) -> bool:
    """Atomically publish once: RPUSH the stamp + HSET the hash fields, then
    PUBLISH a trigger for OF -- all skipped if this exact signal was already
    published before. One EVAL round-trip.
    """
    list_key = signal_list_key(key_prefix, symbol, timeframe, strategy)
    hash_key = signal_hash_key(list_key, stamp)
    event_message = signal_event_message(symbol, timeframe, strategy, stamp)
    args: list[str] = [
        stamp,
        str(int(retention_seconds)),
        str(int(max_list_entries)),
        event_channel,
        event_message,
    ]
    for field, value in hash_fields.items():
        args.append(field)
        args.append(value)
    created = client.eval(_PUBLISH_SIGNAL_SCRIPT, 2, hash_key, list_key, *args)
    return bool(int(created))


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
    if "sl" in payload:
        lines.append(f"SL: `{_display_number(payload['sl'])}`")
    if "sl_dow" in payload:
        lines.append(f"SL Dow: `{_display_number(payload['sl_dow'])}`")
    if "tp" in payload:
        lines.append(f"TP: `{_display_number(payload['tp'])}`")
    lines.extend(
        [
            f"ATR: `{_display_number(payload['atr'])}`",
            f"Valid until: `{payload['valid_until']}`",
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
