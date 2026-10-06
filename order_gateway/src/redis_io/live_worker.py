"""Event-driven OG worker: Redis DB0 candles -> core strategy -> Redis DB1."""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable
from typing import Any

import pandas as pd
import redis

from order_gateway.src.configuration import (
    CONFIG,
    get_strategy,
    run_strategy,
    run_strategy_with_trend_reference,
    trend_reference_for,
)
from order_gateway.src.log import configure_logging, set_log_level
from order_gateway.src.redis_io.candle_reader import (
    create_client,
    normalize_app_config,
    read_candles_from_redis,
    scan_candle_pairs,
)
from order_gateway.src.redis_io.event_listener import listen_for_candle_hash_events
from order_gateway.src.redis_io.signal_publisher import (
    as_utc,
    build_signal,
    publish_signal,
    send_discord_signal,
    to_hash_fields,
)

LOGGER = logging.getLogger("live_worker")
# Level dùng trong lúc khởi động, trước khi đọc được live.log_level thật từ
# og_config.yaml. Chỉ để lỗi config kịp vào file log.
_BOOTSTRAP_LOG_LEVEL = "INFO"


def _handle_shutdown_signal(signum: int, frame: object) -> None:
    """SIGTERM handler -- systemctl stop gửi SIGTERM, không phải Ctrl+C
    (KeyboardInterrupt), nên trước đây không có dòng log nào ghi lại việc
    dừng service qua đường chính thống. Raise SystemExit để chạy được các
    khối `finally` đang đóng kết nối Redis (run_forever), rồi main() bắt
    lại để log dòng đóng vòng đời.
    """
    LOGGER.info("event=shutdown_signal signal=%s", signal.Signals(signum).name)
    raise SystemExit(0)


def timeframe_duration(timeframe: str) -> pd.Timedelta:
    """Convert an OG timeframe code into an exact duration."""
    code = timeframe.strip().upper()
    units = {"M": "minutes", "H": "hours", "D": "days", "W": "weeks"}
    unit = units.get(code[:1])
    amount_text = code[1:] or "1"
    if unit is None or not amount_text.isdigit() or int(amount_text) <= 0:
        raise ValueError(f"Unsupported timeframe '{timeframe}'")
    return pd.Timedelta(**{unit: int(amount_text)})


def signal_window(
    strategy: str,
    timeframe: str,
    bartime: Any,
    rule: dict[str, Any],
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Calculate validity from candle open time and the strategy rule."""
    duration = timeframe_duration(timeframe)
    valid_from = as_utc(bartime) + duration
    mode = str(rule["mode"]).lower()
    if mode == "next_bar":
        valid_until = valid_from + duration * int(rule["valid_bars"])
    elif mode == "seconds_after_bar_close":
        valid_until = valid_from + pd.Timedelta(seconds=int(rule["seconds"]))
    else:
        raise ValueError(f"Unsupported validity mode for '{strategy}': {mode}")
    return valid_from, valid_until


def strategies_for_timeframe(
    enabled_strategies: list[str],
    timeframe: str,
) -> list[str]:
    """Route a candle event using each strategy's existing timeframe config."""
    timeframe = timeframe.upper()
    matches: list[str] = []
    for key in enabled_strategies:
        spec = get_strategy(key)
        live_timeframes = spec.recommended_timeframes or spec.supported_timeframes
        if timeframe in live_timeframes:
            matches.append(spec.key)
    return matches


def process_candle_update(
    input_client: redis.Redis,
    output_client: redis.Redis,
    *,
    timeframe: str,
    symbol: str,
    config: dict[str, Any],
    now: pd.Timestamp | None = None,
    strategy_runner: Callable[..., pd.DataFrame] = run_strategy,
    trend_runner: Callable[..., pd.DataFrame] = run_strategy_with_trend_reference,
) -> int:
    """Process one changed snapshot and return the number of new DB1 keys.

    Mỗi strategy chạy 1 trong 2 đường, tuỳ `trend_filter_enabled` của nó
    trong og_config.yaml (xem configuration.trend_reference_for):
      - tắt (mặc định): run_strategy() trên đúng 1 timeframe — y như trước.
      - bật: đọc THÊM nến trend timeframe của cùng symbol từ DB0 rồi chạy
        run_strategy_with_trend_reference(), chỉ giữ tín hiệu cùng chiều trend.
    """
    config = normalize_app_config(config)
    redis_config = config["redis"]
    input_config = redis_config["input"]
    output_config = redis_config["output"]
    live_config = config["live"]
    strategy_keys = strategies_for_timeframe(
        list(live_config["enabled_strategies"]),
        timeframe,
    )
    if not strategy_keys:
        return 0

    candles = read_candles_from_redis(
        input_client,
        symbol=symbol,
        timeframe=timeframe,
        key_prefix=input_config["key_prefix"],
        snapshot_bars=int(input_config["snapshot_bars"]),
    )
    if candles.empty:
        LOGGER.warning("event=empty_snapshot symbol=%s timeframe=%s", symbol, timeframe)
        return 0

    published = 0
    current_time = as_utc(now if now is not None else pd.Timestamp.now(tz="UTC"))
    for strategy in strategy_keys:
        trend_enabled, trend_tf = trend_reference_for(strategy)
        if trend_enabled:
            trend_candles = read_candles_from_redis(
                input_client,
                symbol=symbol,
                timeframe=trend_tf,
                key_prefix=input_config["key_prefix"],
                snapshot_bars=int(input_config["snapshot_bars"]),
            )
            if trend_candles.empty:
                # Operator đã yêu cầu lọc theo trend mà DB0 không có nến khung
                # đó cho symbol này -> BỎ QUA, tuyệt đối không publish tín hiệu
                # chưa lọc (im lặng bỏ qua bộ lọc còn tệ hơn là không có tín
                # hiệu: OF sẽ đặt lệnh mà operator tin là đã được lọc).
                LOGGER.error(
                    "event=trend_snapshot_missing strategy=%s symbol=%s timeframe=%s "
                    "trend_tf=%s",
                    strategy, symbol, timeframe, trend_tf,
                )
                continue
            result = trend_runner(
                strategy,
                symbol=symbol,
                entry_tf=timeframe,
                entry_bars=candles,
                trend_tf=trend_tf,
                trend_bars=trend_candles,
            )
        else:
            result = strategy_runner(
                strategy,
                symbol=symbol,
                tf=timeframe,
                bars=candles,
            )
        latest = result.iloc[-1]
        signal = int(latest["signal"])
        if signal == 0:
            if trend_enabled and int(latest.get("raw_signal", 0)) != 0:
                # Có tín hiệu gốc nhưng trend khung lớn không cùng chiều —
                # đây là bộ lọc đang LÀM VIỆC, không phải "không có gì".
                LOGGER.info(
                    "event=signal_filtered_by_trend strategy=%s symbol=%s timeframe=%s "
                    "trend_tf=%s raw_signal=%s trend_bias=%s status=%s",
                    strategy, symbol, timeframe, trend_tf,
                    int(latest["raw_signal"]), latest.get("trend_bias"),
                    latest.get("trend_filter_status"),
                )
            else:
                LOGGER.debug(
                    "event=no_signal strategy=%s symbol=%s timeframe=%s",
                    strategy, symbol, timeframe,
                )
            continue

        valid_from, valid_until = signal_window(
            strategy,
            timeframe,
            latest["bartime"],
            live_config["signal_validity"][strategy],
        )
        if current_time < valid_from:
            LOGGER.warning(
                "event=candle_not_closed strategy=%s symbol=%s timeframe=%s",
                strategy, symbol, timeframe,
            )
            continue
        if current_time >= valid_until:
            LOGGER.info(
                "event=signal_expired strategy=%s symbol=%s timeframe=%s",
                strategy, symbol, timeframe,
            )
            continue

        signal_id, stamp, payload = build_signal(
            strategy=strategy,
            timeframe=timeframe,
            symbol=symbol,
            row=latest,
            valid_from=valid_from,
            valid_until=valid_until,
        )
        hash_fields = to_hash_fields(strategy, payload)
        created = publish_signal(
            output_client,
            key_prefix=output_config["key_prefix"],
            channel_prefix=str(output_config["channel_prefix"]),
            symbol=symbol,
            timeframe=timeframe,
            strategy=strategy,
            stamp=stamp,
            hash_fields=hash_fields,
            retention_seconds=int(output_config["retention_seconds"]),
            max_list_entries=int(output_config["retention_max_entries"]),
        )
        if created:
            published += 1
            LOGGER.info(
                "event=signal_published strategy=%s symbol=%s timeframe=%s side=%s signal_id=%s",
                strategy, symbol, timeframe, payload["side"], signal_id,
            )
            discord_config = live_config["discord"]
            if discord_config["enabled"]:
                sent, detail = send_discord_signal(payload, discord_config)
                if sent:
                    LOGGER.info("event=discord_sent signal_id=%s", signal_id)
                else:
                    LOGGER.error(
                        "event=discord_failed signal_id=%s detail=%s",
                        signal_id, detail,
                    )
        else:
            LOGGER.info(
                "event=signal_duplicate strategy=%s symbol=%s timeframe=%s signal_id=%s",
                strategy, symbol, timeframe, signal_id,
            )
    return published


def _validate_config(config: dict[str, Any]) -> None:
    config = normalize_app_config(config)
    redis_config = config["redis"]
    input_config = redis_config["input"]
    output_config = redis_config["output"]
    live_config = config["live"]

    if int(input_config["db"]) == int(output_config["db"]):
        raise ValueError("Redis input and output must use different databases")
    if int(input_config["snapshot_bars"]) <= 0:
        raise ValueError("redis.input.snapshot_bars must be positive")
    if int(input_config["reconcile_interval_seconds"]) <= 0:
        raise ValueError("redis.input.reconcile_interval_seconds must be positive")
    if not str(output_config["key_prefix"]).strip():
        raise ValueError("redis.output.key_prefix must be configured")
    if not str(output_config["channel_prefix"]).strip():
        raise ValueError("redis.output.channel_prefix must be configured")
    if int(output_config["retention_seconds"]) <= 0:
        raise ValueError("redis.output.retention_seconds must be positive")
    if int(output_config["retention_max_entries"]) <= 0:
        raise ValueError("redis.output.retention_max_entries must be positive")

    discord_config = live_config["discord"]
    if discord_config["enabled"] and not str(discord_config["webhook_url"]).strip():
        raise ValueError("live.discord.webhook_url is required when Discord is enabled")
    if float(discord_config["timeout_seconds"]) <= 0:
        raise ValueError("live.discord.timeout_seconds must be positive")
    if int(discord_config["retry_count"]) < 0:
        raise ValueError("live.discord.retry_count cannot be negative")
    if float(discord_config["retry_delay_seconds"]) < 0:
        raise ValueError("live.discord.retry_delay_seconds cannot be negative")

    for key in [str(item).lower() for item in live_config["enabled_strategies"]]:
        spec = get_strategy(key)
        timeframes = spec.recommended_timeframes or spec.supported_timeframes
        if not timeframes:
            raise ValueError(f"Strategy '{key}' has no live timeframes")
        signal_window(
            key,
            timeframes[0],
            pd.Timestamp("2026-01-01", tz="UTC"),
            live_config["signal_validity"][key],
        )


def redis_enabled(config: dict[str, Any]) -> bool:
    """Return the explicit operator switch for the Redis signal pipeline."""
    config = normalize_app_config(config)
    enabled = config["redis"]["enabled"]
    if not isinstance(enabled, bool):
        raise TypeError("redis.enabled must be true or false")
    return enabled


def run_forever(config: dict[str, Any]) -> None:
    """Connect, reconcile current keys, then process Pub/Sub candle events forever."""
    config = normalize_app_config(config)
    _validate_config(config)
    redis_config = config["redis"]
    input_config = redis_config["input"]
    output_config = redis_config["output"]
    live_config = config["live"]

    while True:
        clients: list[redis.Redis] = []
        try:
            input_client = create_client(redis_config, input_config["db"])
            event_client = create_client(redis_config, input_config["db"])
            output_client = create_client(redis_config, output_config["db"])
            clients = [input_client, event_client, output_client]
            for client in clients:
                client.ping()
            def process(
                timeframe: str,
                symbol: str,
                _input_client: redis.Redis = input_client,
                _output_client: redis.Redis = output_client,
            ) -> int:
                try:
                    return process_candle_update(
                        _input_client,
                        _output_client,
                        timeframe=timeframe,
                        symbol=symbol,
                        config=config,
                    )
                except redis.RedisError:
                    raise
                except Exception:
                    LOGGER.exception(
                        "event=process_failed symbol=%s timeframe=%s", symbol, timeframe,
                    )
                    return 0

            def reconcile_known_pairs(_input_client: redis.Redis = input_client) -> None:
                # Used both at startup (gated by process_on_startup) and on the
                # periodic reconcile_interval_seconds tick below -- safe either
                # way, since process_candle_update() only ever publishes a
                # signal still inside its own [valid_from, valid_until) window,
                # regardless of what triggered the re-scan.
                start = time.monotonic()
                LOGGER.info("event=reconcile_start db=%s", input_config["db"])
                pairs = sorted(
                    set(scan_candle_pairs(_input_client, input_config["key_prefix"]))
                )
                processed = 0
                published_total = 0
                for symbol, timeframe in pairs:
                    if strategies_for_timeframe(
                        list(live_config["enabled_strategies"]), timeframe
                    ):
                        published_total += process(timeframe, symbol)
                        processed += 1
                LOGGER.info(
                    "event=reconcile_end pairs=%d published=%d duration_s=%.1f",
                    processed, published_total, time.monotonic() - start,
                )

            LOGGER.info(
                "event=redis_connected input_db=%s output_db=%s",
                input_config["db"], output_config["db"],
            )
            # OG trigger dựa thẳng vào keyspace notification của Redis trên
            # DB0 (xem event_listener.py) -- cần server bật đúng lớp 'K'+'h'.
            # Raise rõ ràng ngay đây nếu thiếu, không âm thầm chạy mà không
            # bao giờ nhận được event nào (chỉ còn sống nhờ reconcile 30
            # phút, dễ bị hiểu nhầm là "bình thường" vì service vẫn active).
            notify_flags = str(
                event_client.config_get("notify-keyspace-events").get(
                    "notify-keyspace-events", ""
                )
            )
            missing = [c for c in ("K", "h") if c not in notify_flags]
            if missing:
                raise RuntimeError(
                    f"Redis notify-keyspace-events thiếu lớp {missing} (hiện tại: "
                    f"'{notify_flags}') -- chạy CONFIG SET notify-keyspace-events "
                    f"'{notify_flags}{''.join(missing)}' rồi CONFIG REWRITE để lưu bền."
                )
            LOGGER.info(
                "event=subscribed db=%s key_prefix=%s notify_flags=%s",
                input_config["db"], input_config["key_prefix"], notify_flags,
            )
            listen_for_candle_hash_events(
                event_client,
                db=int(input_config["db"]),
                key_prefix=str(input_config["key_prefix"]),
                on_update=process,
                on_started=(
                    reconcile_known_pairs if input_config["process_on_startup"] else None
                ),
                reconcile_interval_seconds=int(input_config["reconcile_interval_seconds"]),
                on_reconcile=reconcile_known_pairs,
            )
        except redis.RedisError as exc:
            delay = float(redis_config["reconnect_delay_seconds"])
            LOGGER.error(
                "event=redis_connection_lost error=%s retry_in_s=%s", exc, delay,
            )
            time.sleep(delay)
        finally:
            for client in clients:
                client.close()


def main() -> int:
    # Bật logging TRƯỚC khi đọc config, và đọc config BÊN TRONG try: nếu
    # og_config.yaml thiếu key (vd 1 trong 8 key redis.input/redis.output bắt
    # buộc), lỗi phải vào được file log dưới dạng event=config_error. Trước
    # 2026-09-22 cả 2 bước này nằm ngoài try nên nhánh CRITICAL bên dưới
    # không bao giờ bắn được cho đúng loại lỗi nó tồn tại để ghi lại.
    configure_logging(_BOOTSTRAP_LOG_LEVEL)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    try:
        config = normalize_app_config(CONFIG)
        live_config = config["live"]
        set_log_level(str(live_config["log_level"]))
        LOGGER.info(
            "event=startup strategies=%s log_level=%s redis_input_db=%s redis_output_db=%s",
            ",".join(live_config["enabled_strategies"]),
            live_config["log_level"],
            config["redis"]["input"]["db"],
            config["redis"]["output"]["db"],
        )
        # Ghi rõ trạng thái trend filter từng strategy: bật/tắt là quyết định
        # vận hành ảnh hưởng trực tiếp số tín hiệu, phải thấy được trong log
        # thay vì phải mở og_config.yaml ra đối chiếu.
        for key in live_config["enabled_strategies"]:
            enabled, trend_tf = trend_reference_for(str(key))
            LOGGER.info(
                "event=trend_filter strategy=%s enabled=%s trend_tf=%s",
                str(key).lower(), enabled, trend_tf if enabled else "-",
            )
        if not redis_enabled(config):
            LOGGER.info("event=disabled reason=redis_disabled_by_config")
            return 0
        run_forever(config)
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("event=stopped")
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        LOGGER.critical("event=config_error error=%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
