"""Build OG signals, store them in Redis DB1 (L_OG_* LIST/HASH) and push them
to OF over Pub/Sub (L_CHANNEL_* channels -- a transport only, nothing stored)."""

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


def _optional_number(row: Any, column: str) -> float | None:
    try:
        value = float(row[column])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return round(value, 2)


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
    # có cột này nên _optional_number trả None và field bị bỏ qua. SL/TP
    # không có ở đây: OF tự tính từ bảng KSL/KTP của họ (2026-10-04).
    sl_dow = _optional_number(row, "sl_dow")
    if sl_dow is not None:
        payload["sl_dow"] = sl_dow
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
         tradeSide/orderType/stopPrice/expirationTimestamp/clientOrderId/
         comment. `expirationTimestamp` is
         epoch seconds (not ISO) to match cTrader's int64 type exactly.
         `orderType`'s NAME matches cTrader but its value here is still a
         plain string ("STOP"/"MARKET") -- OF must still map that to the
         protobuf enum, name alignment alone cannot remove that step.
         `symbol` here is OG's plain string, never cTrader's numeric
         `symbolId` -- that mapping is entirely OF's to do.
      3. OF-support fields, not part of cTrader's API at all -- atr (OF's
         own volume/position-sizing input and SL/TP distance base: OF
         computes SL/TP itself from its own KSL/KTP table times this atr),
         valid_from (OF's own freshness gate before it even calls the API).
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
    return fields


def strategy_token(strategy: str) -> str:
    """The STRATEGY token used in Redis key and channel names: upper-case with
    no underscore, so ``ma_cross`` becomes ``MACROSS`` and no token ever
    contains the ``_`` delimiter (chốt 2026-10-05). Names only -- the
    ``strategy`` field and ``clientOrderId`` inside the HASH/message keep
    the strategy's real name (``ma_cross``).
    """
    return strategy.upper().replace("_", "")


def og_list_key(key_prefix: str, strategy: str, symbol: str, timeframe: str) -> str:
    """The OG LIST of stamps for one (strategy, symbol, timeframe), oldest
    first -- ``{key_prefix}_{STRATEGY}_{SYMBOL}_{TIMEFRAME}``, e.g.
    ``L_OG_COMBO_US30_H4`` / ``L_OG_MACROSS_US30_M30``. Underscore join, zero
    colons, STRATEGY first (chốt 2026-10-04).
    """
    return f"{key_prefix}_{strategy_token(strategy)}_{symbol.upper()}_{timeframe.upper()}"


def channel_name(channel_prefix: str, strategy: str, symbol: str, timeframe: str) -> str:
    """The Pub/Sub channel one (strategy, symbol, timeframe) signal is pushed
    on, e.g. ``L_CHANNEL_COMBO_US30_H4`` / ``L_CHANNEL_MACROSS_US30_M30``. OF
    subscribes to exactly the channels it follows (or a pattern such as
    ``L_CHANNEL_COMBO_*``), so Redis does the filtering.

    A channel is a transport, not storage: nothing is kept under this name
    and it belongs to no DB number. The persisted record of a signal is the
    L_OG LIST/HASH in DB1 (see ``og_list_key``).
    """
    return f"{channel_prefix}_{strategy_token(strategy)}_{symbol.upper()}_{timeframe.upper()}"


def stamp_hash_key(list_key: str, stamp: str) -> str:
    """The HASH holding one signal: the list's key plus ``":" + stamp``,
    the exact same one-concatenation rule DP uses for DB0's own HASHes.
    """
    return f"{list_key}:{stamp}"


# KEYS[1]=og_hash KEYS[2]=og_list
# ARGV[1]=stamp ARGV[2]=retention_seconds ARGV[3]=max_list_entries
# ARGV[4]=channel ARGV[5]=message ARGV[6..]=field1 value1 field2 value2 ...
#
# "Already published?" is answered by the OG HASH existing -- a duplicate
# (retried event, reconcile re-scan) writes nothing and publishes nothing, so
# OF is never notified twice for the same signal. Otherwise, in one atomic
# round trip: the OG HASH (retention_seconds TTL) and LIST (bounded by LTRIM
# to max_list_entries) are written first, then the full message is PUBLISHed
# on the per-(strategy, symbol, timeframe) channel. The channel stores
# nothing: an OF that missed the at-most-once Pub/Sub delivery reads the
# signal back from the OG LIST/HASH, which carry the same fields.
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


def signal_message(hash_fields: dict[str, str]) -> str:
    """The Pub/Sub message: the complete signal, i.e. exactly the same fields
    as the OG HASH (chốt 2026-10-04: message carries full info, not just a
    trigger)."""
    return json.dumps(hash_fields, ensure_ascii=False)


def publish_signal(
    client: redis.Redis,
    *,
    key_prefix: str,
    channel_prefix: str,
    symbol: str,
    timeframe: str,
    strategy: str,
    stamp: str,
    hash_fields: dict[str, str],
    retention_seconds: int,
    max_list_entries: int,
) -> bool:
    """Atomically publish once: OG HASH/LIST + PUBLISH the full message on the
    signal's Pub/Sub channel -- all skipped if this exact signal was already
    published. One EVAL round-trip. Returns True only when it actually wrote.
    """
    og_list = og_list_key(key_prefix, strategy, symbol, timeframe)
    channel = channel_name(channel_prefix, strategy, symbol, timeframe)
    args: list[str] = [
        stamp,
        str(int(retention_seconds)),
        str(int(max_list_entries)),
        channel,
        signal_message(hash_fields),
    ]
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
    if "sl_dow" in payload:
        lines.append(f"SL Dow: `{_display_number(payload['sl_dow'])}`")
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
