"""Event-driven des worker: Redis input-DB candles -> core strategy -> Redis output-DB.

Same flow as the live OG worker with ONE deliberate difference (decision 2026-10-07): signals
never expire. There is no valid-until window, no TTL and no list trimming, so every signal
found on the replayed candles is kept. The "candle not closed yet" check stays.

Simulation-only addition (decision 2026-10-08): every START of the process deletes the
previous run's signals (``{output.key_prefix}_*`` in the output DB) before subscribing,
so a replay never mixes with leftovers. A mere Redis reconnect does not clear again.
"""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable
from typing import Any

import pandas as pd
import redis
from wisdom_engine.des.src.configuration import (
    COMBO_SYMBOL_X,
    CONFIG,
    alternating_filter_for,
    check_trend_timeframe,
    get_strategy,
    run_strategy,
    run_strategy_with_trend_reference,
    trend_methods_for,
    trend_reference_for,
)
from wisdom_engine.des.src.log import configure_logging, set_log_level
from wisdom_engine.des.src.redis_io.candle_reader import (
    create_client,
    normalize_app_config,
    read_candles_from_redis,
    scan_candle_pairs,
)
from wisdom_engine.des.src.redis_io.event_listener import listen_for_candle_hash_events
from wisdom_engine.des.src.redis_io.signal_publisher import (
    as_utc,
    build_signal,
    clear_signals,
    publish_signal,
    send_discord_signal,
    to_hash_fields,
)

LOGGER = logging.getLogger("live_worker")
# Level dùng trong lúc khởi động, trước khi đọc được live.log_level thật từ
# des_config.yaml. Chỉ để lỗi config kịp vào file log.
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


def signal_valid_from(timeframe: str, bartime: Any) -> pd.Timestamp:
    """The moment a signal can act: the candle's CLOSE time (open time + one bar).

    des applies no expiry, so unlike the live OG there is no valid-until -- only
    this earliest moment, which still guards against a candle that has not
    closed yet.
    """
    return as_utc(bartime) + timeframe_duration(timeframe)


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


def _publish_signal_row(
    output_client: redis.Redis,
    *,
    key_prefix: str,
    channel_prefix: str | None,
    strategy: str,
    timeframe: str,
    symbol: str,
    row: pd.Series,
    valid_from: pd.Timestamp,
) -> tuple[bool, str, dict[str, Any]]:
    """Dựng tín hiệu từ dòng cuối rồi ghi vào MỘT đầu ra (HASH + LIST; chỉ publish Pub/Sub khi có channel_prefix).

    Trả về (đã ghi mới?, signal_id, payload). Tín hiệu đã có sẵn trong đầu ra đó thì không ghi lại.
    """
    signal_id, stamp, payload = build_signal(
        strategy=strategy, timeframe=timeframe, symbol=symbol, row=row, valid_from=valid_from,
    )
    created = publish_signal(
        output_client,
        key_prefix=key_prefix,
        channel_prefix=channel_prefix,
        symbol=symbol,
        timeframe=timeframe,
        strategy=strategy,
        stamp=stamp,
        hash_fields=to_hash_fields(strategy, payload),
    )
    return created, signal_id, payload


def _process_trend_output(
    input_client: redis.Redis,
    trend_output_client: redis.Redis,
    *,
    strategy: str,
    timeframe: str,
    symbol: str,
    candles: pd.DataFrame,
    input_config: dict[str, Any],
    trend_output_config: dict[str, Any],
    valid_from: pd.Timestamp,
    trend_runner: Callable[..., pd.DataFrame],
) -> int:
    """Đầu ra TREND (giống DB1 của we_core): đọc THÊM nến khung trend của ô (symbol, khung vào lệnh) từ DB đầu vào rồi
    chạy run_strategy_with_trend_reference() (tự ép lọc trend BẬT, bất kể cờ trend_filter_enabled); chỉ tín hiệu cùng chiều
    trend được ghi, và KHÔNG publish Pub/Sub (đầu ra này chỉ để hiển thị). Trả về số tín hiệu ghi mới (0 hoặc 1)."""
    trend_tf = trend_reference_for(strategy, symbol, timeframe)[1]
    if not trend_tf:
        LOGGER.error(
            "event=trend_tf_missing strategy=%s symbol=%s timeframe=%s", strategy, symbol, timeframe,
        )
        return 0
    trend_candles = read_candles_from_redis(
        input_client,
        symbol=symbol,
        timeframe=trend_tf,
        key_prefix=input_config["key_prefix"],
        snapshot_bars=int(input_config["snapshot_bars"]),
    )
    if trend_candles.empty:
        # Đầu ra trend phải là tín hiệu ĐÃ lọc: DB đầu vào không có nến khung trend thì BỎ QUA, tuyệt đối không ghi
        # tín hiệu chưa lọc vào đầu ra này.
        LOGGER.error(
            "event=trend_snapshot_missing strategy=%s symbol=%s timeframe=%s trend_tf=%s",
            strategy, symbol, timeframe, trend_tf,
        )
        return 0
    result = trend_runner(
        strategy,
        symbol=symbol,
        entry_tf=timeframe,
        entry_bars=candles,
        trend_tf=trend_tf,
        trend_bars=trend_candles,
    )
    latest = result.iloc[-1]
    if int(latest["signal"]) == 0:
        # Có tín hiệu gốc nhưng trend khung lớn không cùng chiều — đây là bộ lọc đang LÀM VIỆC.
        LOGGER.debug(
            "event=signal_filtered_by_trend strategy=%s symbol=%s timeframe=%s "
            "trend_tf=%s raw_signal=%s trend_bias=%s status=%s",
            strategy, symbol, timeframe, trend_tf,
            int(latest["raw_signal"]), latest.get("trend_bias"), latest.get("trend_filter_status"),
        )
        return 0
    created, signal_id, payload = _publish_signal_row(
        trend_output_client,
        key_prefix=trend_output_config["key_prefix"],
        channel_prefix=None,
        strategy=strategy,
        timeframe=timeframe,
        symbol=symbol,
        row=latest,
        valid_from=valid_from,
    )
    if created:
        LOGGER.info(
            "event=trend_signal_published strategy=%s symbol=%s timeframe=%s trend_tf=%s side=%s signal_id=%s",
            strategy, symbol, timeframe, trend_tf, payload["side"], signal_id,
        )
        return 1
    LOGGER.info(
        "event=trend_signal_duplicate strategy=%s symbol=%s timeframe=%s signal_id=%s",
        strategy, symbol, timeframe, signal_id,
    )
    return 0


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
    trend_output_client: redis.Redis | None = None,
) -> int:
    """Process one changed snapshot and return the number of new signals written (mọi đầu ra cộng lại).

    Mỗi strategy có HAI đầu ra, giống DB0/DB1 của we_core:
      - đầu ra chính (``output_client``): run_strategy() trên đúng 1 timeframe, KHÔNG lọc trend — y như trước
        (cờ trend_filter_enabled giữ nghĩa của we_core: chỉ là mặc định của đường 1 khung; xem _validate_config);
      - đầu ra trend (``trend_output_client``; None = không ghi): chỉ tính khi nến cuối có tín hiệu thô
        (raw_signal != 0 — tín hiệu sau lọc trend luôn là tập con của raw_signal nên không tốn thêm khi không có
        tín hiệu), xem _process_trend_output.
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
        result = strategy_runner(
            strategy,
            symbol=symbol,
            tf=timeframe,
            bars=candles,
        )
        latest = result.iloc[-1]
        signal = int(latest["signal"])
        raw_signal = int(latest["raw_signal"])
        if signal == 0 and raw_signal == 0:
            LOGGER.debug(
                "event=no_signal strategy=%s symbol=%s timeframe=%s",
                strategy, symbol, timeframe,
            )
            continue

        valid_from = signal_valid_from(timeframe, latest["bartime"])
        if current_time < valid_from:
            LOGGER.warning(
                "event=candle_not_closed strategy=%s symbol=%s timeframe=%s",
                strategy, symbol, timeframe,
            )
            continue

        if signal != 0:
            created, signal_id, payload = _publish_signal_row(
                output_client,
                key_prefix=output_config["key_prefix"],
                channel_prefix=str(output_config["channel_prefix"]),
                strategy=strategy,
                timeframe=timeframe,
                symbol=symbol,
                row=latest,
                valid_from=valid_from,
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

        if trend_output_client is not None and raw_signal != 0:
            try:
                published += _process_trend_output(
                    input_client,
                    trend_output_client,
                    strategy=strategy,
                    timeframe=timeframe,
                    symbol=symbol,
                    candles=candles,
                    input_config=input_config,
                    trend_output_config=redis_config["output_trend"],
                    valid_from=valid_from,
                    trend_runner=trend_runner,
                )
            except redis.RedisError:
                raise
            except Exception:
                # Đầu ra trend chỉ để hiển thị: lỗi riêng của nó (vd khối trend gặp dữ liệu lạ) không được làm mất
                # đầu ra chính của các chiến lược còn lại trên cùng nến.
                LOGGER.exception(
                    "event=trend_output_failed strategy=%s symbol=%s timeframe=%s", strategy, symbol, timeframe,
                )
    return published


def check_trend_timeframes(enabled_strategies: list[str]) -> None:
    """Kiểm cấu hình TRƯỚC khi des chạm vào Redis: khung trend của MỌI (chiến lược, symbol, khung
    vào lệnh) mà des xử lý phải >= khung vào lệnh (configuration.check_trend_timeframe, cùng quy tắc với
    we_core). Khung trend là ô khai trong `trend_tf_by_symbol` (đủ ô được bảo đảm lúc nạp config).

    Gọi trong _validate_config nên chạy trước clear_signals(): cấu hình sai thì dừng ngay với một thông
    báo liệt kê mọi tổ hợp sai (ValueError -> config_error), db tín hiệu còn nguyên.
    """
    wrong: dict[tuple[str, str, str], list[str]] = {}
    for key in [str(item).lower() for item in enabled_strategies]:
        spec = get_strategy(key)
        for timeframe in spec.recommended_timeframes or spec.supported_timeframes:
            for symbol in sorted(COMBO_SYMBOL_X):
                trend_tf = trend_reference_for(key, symbol, timeframe)[1]
                try:
                    check_trend_timeframe(timeframe, trend_tf)
                except ValueError as exc:
                    wrong.setdefault((key, timeframe, str(exc)), []).append(symbol)
    if wrong:
        details = [
            f"{key} {timeframe}: {message} [{', '.join(symbols)}]"
            for (key, timeframe, message), symbols in wrong.items()
        ]
        raise ValueError(
            "trend timeframe in des_config.yaml (trend_tf_by_symbol) is smaller "
            "than the entry timeframe — " + "; ".join(details)
        )


def _validate_config(config: dict[str, Any]) -> None:
    config = normalize_app_config(config)
    redis_config = config["redis"]
    input_config = redis_config["input"]
    output_config = redis_config["output"]
    trend_output_config = redis_config["output_trend"]
    live_config = config["live"]

    if len({int(input_config["db"]), int(output_config["db"]), int(trend_output_config["db"])}) != 3:
        raise ValueError("Redis input, output and output_trend must use three different databases")
    if int(input_config["snapshot_bars"]) <= 0:
        raise ValueError("redis.input.snapshot_bars must be positive")
    if int(input_config["reconcile_interval_seconds"]) <= 0:
        raise ValueError("redis.input.reconcile_interval_seconds must be positive")
    if not str(output_config["key_prefix"]).strip():
        raise ValueError("redis.output.key_prefix must be configured")
    if not str(output_config["channel_prefix"]).strip():
        raise ValueError("redis.output.channel_prefix must be configured")
    if not str(trend_output_config["key_prefix"]).strip():
        raise ValueError("redis.output_trend.key_prefix must be configured")

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
        if trend_reference_for(key)[0]:
            # Đầu ra chính luôn là đường 1 khung KHÔNG lọc trend; đầu ra trend tự ép lọc bật. Cờ true sẽ làm đường 1 khung
            # báo lỗi ở từng sự kiện (cần khung trend), nên chặn ngay lúc khởi động.
            raise ValueError(
                f"strategies.{key}.trend_filter_enabled must be false in des: the main output is always unfiltered "
                "and the trend output is produced separately (redis.output_trend)"
            )
    check_trend_timeframes(list(live_config["enabled_strategies"]))


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
    trend_output_config = redis_config["output_trend"]
    live_config = config["live"]
    # Mỗi lần START tiến trình bắt đầu từ DB tín hiệu rỗng (chốt 2026-10-08); chỉ
    # reconnect Redis trong cùng tiến trình thì KHÔNG xoá lại.
    signals_cleared = False

    while True:
        clients: list[redis.Redis] = []
        try:
            input_client = create_client(redis_config, input_config["db"])
            event_client = create_client(redis_config, input_config["db"])
            output_client = create_client(redis_config, output_config["db"])
            trend_output_client = create_client(redis_config, trend_output_config["db"])
            clients = [input_client, event_client, output_client, trend_output_client]
            for client in clients:
                client.ping()
            def process(
                timeframe: str,
                symbol: str,
                _input_client: redis.Redis = input_client,
                _output_client: redis.Redis = output_client,
                _trend_output_client: redis.Redis = trend_output_client,
            ) -> int:
                try:
                    return process_candle_update(
                        _input_client,
                        _output_client,
                        timeframe=timeframe,
                        symbol=symbol,
                        config=config,
                        trend_output_client=_trend_output_client,
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
                # periodic reconcile_interval_seconds tick below. des has no
                # expiry window, so a re-scan can publish the newest candle's
                # signal if it was missed earlier; a signal already published
                # is never written twice (the OG HASH existing = already
                # published), so the re-scan is safe either way.
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
                "event=redis_connected input_db=%s output_db=%s output_trend_db=%s",
                input_config["db"], output_config["db"], trend_output_config["db"],
            )
            # OG trigger dựa thẳng vào keyspace notification của Redis trên
            # DB đầu vào (xem event_listener.py) -- cần server bật đúng lớp 'K'+'h'.
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
            if not signals_cleared:
                cleared = clear_signals(
                    output_client, key_prefix=str(output_config["key_prefix"])
                )
                LOGGER.info(
                    "event=signals_cleared db=%s key_prefix=%s keys=%d",
                    output_config["db"], output_config["key_prefix"], cleared,
                )
                cleared_trend = clear_signals(
                    trend_output_client, key_prefix=str(trend_output_config["key_prefix"])
                )
                signals_cleared = True
                LOGGER.info(
                    "event=signals_cleared db=%s key_prefix=%s keys=%d",
                    trend_output_config["db"], trend_output_config["key_prefix"], cleared_trend,
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
    # des_config.yaml thiếu key (vd 1 trong 8 key redis.input/redis.output bắt
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
            "event=startup strategies=%s log_level=%s redis_input_db=%s redis_output_db=%s "
            "redis_output_trend_db=%s",
            ",".join(live_config["enabled_strategies"]),
            live_config["log_level"],
            config["redis"]["input"]["db"],
            config["redis"]["output"]["db"],
            config["redis"]["output_trend"]["db"],
        )
        # Ghi rõ cấu hình trend từng strategy: danh sách khối và số ô khung trend quyết định nội dung đầu ra trend
        # (db15) mà tên key không đổi, nên phải thấy được trong log thay vì phải mở des_config.yaml ra đối chiếu.
        # `enabled` là cờ trend_filter_enabled của we_core (đường 1 khung; ở des phải false, xem _validate_config).
        for key in live_config["enabled_strategies"]:
            enabled = trend_reference_for(str(key))[0]
            LOGGER.info(
                "event=trend_filter strategy=%s enabled=%s trend_methods=%s trend_tf_cells=%d",
                str(key).lower(), enabled,
                "+".join(trend_methods_for(str(key))),
                sum(len(cells) for cells in get_strategy(str(key)).trend_tf_by_symbol.values()),
            )
            # Cờ lọc lặp cùng chiều (chỉ strategy nào có cờ): cùng lý do — đổi cờ làm đổi
            # số tín hiệu mà tên key không đổi.
            alternating = alternating_filter_for(str(key))
            if alternating is not None:
                LOGGER.info(
                    "event=alternating_filter strategy=%s enabled=%s", str(key).lower(), alternating,
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
