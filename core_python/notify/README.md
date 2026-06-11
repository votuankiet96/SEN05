# Notify Runtime Architecture

`core_python.notify` owns the realtime signal runtime. It does not own strategy
math; strategies live under `core_python.strategies`.

## Runtime Flow

```text
ws_live commits a closed bar
-> Redis bar_ready event
-> signal_watcher starts runtime loops
-> runtime dispatches symbol/timeframe events
-> detector loads DB data and calls strategy realtime adapter
-> strategy adapter returns normalized realtime signals/messages
-> detector applies common dedup/historical/outbox/alert rules
-> alerts sends Telegram/Discord best-effort notifications
-> delivery_outbox stores pending execution signals
-> runtime relay XADDs pending signals to Redis Streams
```

## Module Ownership

```text
scan_config.py       Production scan groups: strategy, symbols, timeframe, bars.
signal_watcher.py    Thin entrypoint: CLI, warm-up, process lock, thread wiring.
detector.py          Runtime orchestration, signal filtering, dedup, Redis payloads.
runtime.py           Worker queue, bar_ready subscriber, fallback scan, delivery relay.
alerts.py            Telegram/Discord delivery plus legacy formatter re-exports.
state.py             Alert dedup state and warm-up fingerprint persistence.
delivery_outbox.py   Durable pending/delivered execution-signal store.
redis_publisher.py   Redis adapter for bar_ready pub/sub and signal stream XADD.
```

Strategy-specific realtime behavior is outside `notify`:

```text
strategies/ai_trend/realtime.py  AI Trend H3/M45 production semantics.
strategies/combo/realtime.py     Combo raw signal event/message semantics.
strategies/ma_cross/realtime.py  Level-based signal event/message semantics.
strategies/realtime.py           Shared realtime signal contract.
```

## Boundaries

- Dashboard/export paths should continue to use `core_python.engine`.
- Realtime watcher paths should enter through `signal_watcher.py`.
- Redis transport stays isolated in `redis_publisher.py` because it is also used
  by `data_provider.apps.ws_live`.
- Strategy adapters may format strategy-specific messages; `notify.detector`
  should not contain AI Trend, Combo, or MA Cross production semantics.
- Human alerts are best-effort. Execution delivery is protected by
  `delivery_outbox.py` and the runtime relay.
