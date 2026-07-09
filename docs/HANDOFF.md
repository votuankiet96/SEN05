# HANDOFF - OG program after core/past/live split

Date: 2026-07-09

This repo now has three first-class packages:

```text
src/og_core/   Shared market metadata, indicators, strategies, strategy runner,
               and signal event construction. No SQL, Redis, Flask, CSV, or
               service ownership belongs here.

src/og_past/   Historical workflows: SQL Server read adapter, dashboard API,
               chart payloads, and CSV export for backtest/optimize data.

src/og_live/   Realtime workflows: Redis candle_snapshot consumer, 500-bar
               snapshot parser, live signal pipeline, Redis signal publisher,
               local dedup state, and local delivery outbox.
```

## Dependency Direction

```text
og_past  -> og_core
og_live  -> og_core
og_core  -> no og_past / no og_live
```

This is intentional. Strategy changes in `og_core/strategies/**` affect both
historical export and realtime order-generation, while source adapters remain
separate.

## Runtime Entry Points

```bash
# Historical dashboard/export
./.venv/bin/python -m og_past.main --port 8516
og-dashboard --port 8516

# Live Redis signal engine
./.venv/bin/python -m og_live.main
./.venv/bin/python -m og_live.main --once
og-live
```

`--once` on `og_live` is a smoke-test mode: it retries the local outbox, drains
this consumer's pending Redis Stream entries, then waits briefly for at most
one new batch.

## External Contracts

Read `CONTRACTS.md` for the full contract. In short:

- `og_past` reads SQL Server DP6 tables (`DWH.Fact_OHLCV`,
  `DWH.Dim_Timeframe`, `DWH.Dim_Symbol`) through `og_past.data`.
- `og_live` reads Redis stream `candle_snapshot`, where DP6 sends
  `{tv_symbol, tf_code, bars}` and `bars` is a JSON array of OHLCV records.
- `og_live` publishes to `signal_stream:<strategy>`.

## Live Behavior

`og_live` uses the whole snapshot as indicator context, but by default emits
signals only from the latest bar in that snapshot. This avoids replaying old
signals from the 500-bar context window after service restart.

If a Redis publish fails, `og_live` queues the payload into
`runtime/og_live/delivery_outbox.json` and retries periodically. Delivered
signals are recorded in `runtime/og_live/state.json` for local dedup.

## Installation Extras

```bash
./.venv/bin/pip install -e ".[past,live,prod,dev]"
```

Package extras:

- `past`: Flask + pyodbc.
- `live`: redis.
- `prod`: gunicorn.
- `dev`: pytest + ruff + vulture.

## Systemd Templates

Templates are in `deploy/`:

- `deploy/og-dashboard.service`
- `deploy/og-live.service`

Installing/enabling them requires sudo and was not performed by the coding
agent. Expected commands:

```bash
sudo cp deploy/og-dashboard.service /etc/systemd/system/
sudo cp deploy/og-live.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now og-dashboard.service
sudo systemctl enable --now og-live.service
```

## Remaining Production Checks

- Confirm the actual systemd status on vm-og after the owner installs the
  service files.
- Confirm Redis firewall policy only allows the intended DP6 source IP.
- Confirm DP6 applies a bounded MAXLEN policy to `candle_snapshot`.
- Run a deliberate Redis outage test during a safe pilot window and verify DP6
  continues ingesting while `og_live` reconnects cleanly.
- Decide final `OG_LIVE_WATCHED_JSON` for all symbols/timeframes/strategies.
