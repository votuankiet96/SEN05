# Datafeed V3 Operator Runbook

The `python -B src/cli.py ...` commands below are for operating live/backfill
by hand (manual runs, debugging, first-time setup). Unattended 24/7 operation
runs the frozen `run_df/datafeed.exe` under Scheduled Task supervision — see
[Scheduled Task Supervision](#scheduled-task-supervision). A foreground window
left open cannot survive the session that started it; the task can.

## Before Operating

Do not print, paste, or commit `config.yaml`, tokens, cookies, passwords,
connection strings, runtime spool, or auth cache.

Run read-only checks:

```powershell
python -B src/cli.py settings
python -B src/cli.py check-sql
python -B src/cli.py doctor
```

Expected settings highlights:

- `live_pairs = 165`
- `backfill_pairs = 555`
- `live_interval_minutes = 5`
- `live_bars_per_request = 3`
- `closed_candles_only = true`

## Start Live

Pre-flight, then start (dev/test — production runs `run_df/datafeed.exe` via
Scheduled Task, see below):

```powershell
python -B src/cli.py doctor
python -B src/cli.py check-sql
python -B src/cli.py run-live
```

Keep the window open. Live writes:

- `runtime/run/state_live.json`
- `runtime/logs/datafeed_live.log`

Stop live gracefully from another window:

```powershell
python -B src/cli.py stop --mode live
```

## Start Backfill

```powershell
python -B src/cli.py doctor
python -B src/cli.py check-sql
python -B src/cli.py run-backfill
```

Keep the window open. Backfill writes:

- `runtime/run/state_backfill.json`
- `runtime/logs/datafeed_backfill.log`

Stop backfill gracefully from another window:

```powershell
python -B src/cli.py stop --mode backfill
```

## Deep Backfill (manual, one-off)

Pulls old history through TradingView replay and inserts only the candles SQL is
missing. It is not part of the engine: no schedule, off by default
(`deep_backfill.enabled`). Daily backfill must be stopped while it writes.

1. Stop daily backfill (`python -B src/cli.py stop --mode backfill`, or the stop
   marker procedure for the frozen exe) and confirm `state_backfill.json` says `stopped`.
2. Add to `config.yaml` (the section is optional and off by default):

   ```yaml
   deep_backfill:
     enabled: true
     start_utc: 2022-01-01
     symbols: [GOLD]        # e.g. all 11 live symbols once the pilot is verified
   ```

3. Read-only rehearsal, then the real run. Always pass `--config` for the production file:
   without it `python -B src/cli.py` picks the project-root `config.yaml` (the development
   copy, with its own `runtime/`), so the "daily backfill is running" guard would look in the
   wrong place. The summary prints the `config_path` that was used.

   ```powershell
   python -B <repo>\df_core\src\cli.py --config <run_df>\config.yaml deep-backfill --dry-run --symbol GOLD
   python -B <repo>\df_core\src\cli.py --config <run_df>\config.yaml deep-backfill --symbol GOLD
   ```

   The summary reports `inserted` (new rows), `present` (already in SQL) and `changed`
   (in SQL but with different OHLC than TradingView; never rewritten, volume is not compared). Progress is checkpointed per
   pair in `runtime/deep_backfill/state.json`; rerun the same command to resume, `--fresh`
   to walk everything again. Log: `runtime/logs/datafeed_deep_backfill.log`.
4. Set `deep_backfill.enabled: false` again and start the engine so daily backfill
   catches up from the Fact watermark (its request cap is 20,000 candles per pair).

## Status

```powershell
python -B src/cli.py status --mode live
python -B src/cli.py status --mode backfill
```

Healthy status requires:

- `status = running`
- `process_alive = true`
- heartbeat age under 600 seconds

## Auth

Inspect auth readiness:

```powershell
python -B src/cli.py auth status
```

Force refresh only when operating intentionally:

```powershell
python -B src/cli.py auth refresh
```

Auth is fail-closed. If all refresh paths fail, the engine stops instead of
running as guest.

## Backfill Schedule

Backfill schedule is configured in `backfill.schedule_utc`, currently:

```yaml
["11:11", "15:15", "19:19", "23:23", "03:03", "07:07"]
```

The scheduler evaluates these slots across UTC midnight. Backfill yields to
live when live is active, uncertain, or near its next cycle. A dead live PID
does not block backfill forever.

## Logs

- live service: `runtime/logs/datafeed_live.log`
- backfill service: `runtime/logs/datafeed_backfill.log`

Look for structured fields:

- `event=SERVICE_STARTED`
- `event=LIVE_CYCLE_COMPLETED`
- `event=BACKFILL_SCHEDULED`
- `event=PAIR_FAILED`
- `risk=HIGH` or `risk=CRITICAL`

## Cac mode cua datafeed.exe

Lop van hanh. Engine ben trong (core_engine) co co che chiu loi rieng, khong
lien quan den nhung mode nay.

| Goi bang | Lam gi |
|---|---|
| `--run` (hoac double-click khi khong co console) | Chay engine. Bo qua role da co tien trinh song, thay vi fork ra con roi chet tren instance lock |
| double-click tu console | Menu operator |
| `--restart` | Ap `config.yaml` moi: tien kiem -> dung sach -> bat lai **ca live va backfill**. Tien kiem truot thi engine dang chay **khong bi dung** |
| `--restart-live` / `--restart-backfill` | Nhu `--restart` nhung chi dung/bat lai dung 1 role -- live va backfill doc lap hoan toan (live day thang Redis, backfill la nguon ghi SQL duy nhat), nen sua config chi anh huong 1 ben khong can dong tram ben con lai |
| `--watchdog` | 1 luot kiem tra suc khoe. Im khi dung chu dong, canh bao khi hong that, tu restart engine treo (toi da 3 lan/gio) |
| `--doctor-ops` | Bao cao suc khoe lop van hanh (task, tien trinh, chu ky). Chi doc, khong sua |
| `--setup` / `--teardown` | Dang ky / go 2 task chuan, dong thoi go task the he cu |

Doi `config.yaml` ma khong doi code:

```powershell
# sua config.yaml, roi:
.\datafeed.exe --restart
```

Kiem tra lop van hanh khi nghi ngo:

```powershell
.\datafeed.exe --doctor-ops
```

## Scheduled Task Supervision

Production supervision belongs to the frozen deployment in `run_df/`, not to
this source tree. `run_df/install.ps1` registers, under `\SEN05\`:

- **SEN05 Datafeed Engine** — triggers `AtStartup`, runs
  `run_df/datafeed.exe` (which reads `live.enabled` / `backfill.enabled` from
  `config.yaml` and forks whichever workflow(s) are on), restart-on-failure.
  A graceful `datafeed stop` exits 0 so it is never fought; only a real crash
  triggers a restart.
- **SEN05 Datafeed Watchdog** — runs `run_df/datafeed.exe --watchdog` on a
  fixed interval. Distinguishes four states: healthy (silent), intentionally
  stopped (silent; a LOW reminder only after 2h), crashed (one CRITICAL Discord
  alert, edge-triggered), and **hung** — alive but no heartbeat progress. Hung is
  the one failure mode Task Scheduler cannot see (there is no exit code to react
  to), so after a second consecutive detection the watchdog restarts only the
  role(s) actually hung (equivalent to `--restart-live`/`--restart-backfill`,
  not a blanket `--restart` — the healthy role is left running), capped at 3
  auto-restarts per hour total; past the cap it stops trying and alerts that a
  human is needed. Division of labour: Task Scheduler restarts what EXITED,
  the watchdog restarts what is STUCK.

Install / re-register (from inside the copied `run_df/` folder, as Administrator):

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1   # goi datafeed.exe --setup
.\datafeed.exe --setup                               # hoac goi thang
```

`--setup` dang ky ca hai task va **go luon task the he cu** (`SEN05 Datafeed
Live` / `Backfill`, chay qua `.bat` da bi xoa). Task la trong `\SEN05\` chi duoc
bao cao, khong bao gio bi tu dong xoa.

Full deploy steps: `run_df/DEPLOY.md`.

Verify registration:

```powershell
Get-ScheduledTask -TaskPath \SEN05\
```

## Validation Before Code Deployment

```powershell
$env:PYTHONPATH = "src"
python -m pytest test/
Get-ChildItem src -Recurse -Filter *.py | ForEach-Object { python -m py_compile $_.FullName }
python -B src/cli.py check-sql
python -B src/cli.py doctor
```

If a validation gate fails, do not start the new runtime. Keep the evidence and
roll back to the last known commit.

## Rollback

Use Git to return to the approved rollback commit, then run validation again.
Do not delete SQL data, truncate staging tables, reboot the host, or rotate
credentials as part of normal rollback.
