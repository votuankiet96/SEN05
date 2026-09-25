# DP Program V3 Operator Runbook

The `python -m dp_program ...` commands below are for operating live/backfill
by hand (manual runs, debugging, first-time setup). Unattended 24/7 operation
runs the frozen `run_dp/dp_program.exe` under Scheduled Task supervision — see
[Scheduled Task Supervision](#scheduled-task-supervision). A foreground window
left open cannot survive the session that started it; the task can.

## Before Operating

Do not print, paste, or commit `config.yaml`, tokens, cookies, passwords,
connection strings, runtime spool, or auth cache.

Run read-only checks:

```powershell
python -m dp_program settings
python -m dp_program check-sql
python -m dp_program doctor
```

Expected settings highlights:

- `live_pairs = 165`
- `backfill_pairs = 555`
- `live_interval_minutes = 5`
- `live_bars_per_request = 3`
- `closed_candles_only = true`

## Start Live

Pre-flight, then start (dev/test — production runs `run_dp/dp_program.exe` via
Scheduled Task, see below):

```powershell
python -m dp_program doctor
python -m dp_program check-sql
python -m dp_program run-live
```

Keep the window open. Live writes:

- `runtime/run/state_live.json`
- `runtime/logs/dp_program_live.log`

Stop live gracefully from another window:

```powershell
python -m dp_program stop --mode live
```

## Start Backfill

```powershell
python -m dp_program doctor
python -m dp_program check-sql
python -m dp_program run-backfill
```

Keep the window open. Backfill writes:

- `runtime/run/state_backfill.json`
- `runtime/logs/dp_program_backfill.log`

Stop backfill gracefully from another window:

```powershell
python -m dp_program stop --mode backfill
```

## Status

```powershell
python -m dp_program status --mode live
python -m dp_program status --mode backfill
```

Healthy status requires:

- `status = running`
- `process_alive = true`
- heartbeat age under 600 seconds

## Auth

Inspect auth readiness:

```powershell
python -m dp_program auth status
```

Force refresh only when operating intentionally:

```powershell
python -m dp_program auth refresh
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

- live service: `runtime/logs/dp_program_live.log`
- backfill service: `runtime/logs/dp_program_backfill.log`

Look for structured fields:

- `event=SERVICE_STARTED`
- `event=LIVE_CYCLE_COMPLETED`
- `event=BACKFILL_SCHEDULED`
- `event=PAIR_FAILED`
- `risk=HIGH` or `risk=CRITICAL`

## Cac mode cua dp_program.exe

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
.\dp_program.exe --restart
```

Kiem tra lop van hanh khi nghi ngo:

```powershell
.\dp_program.exe --doctor-ops
```

## Scheduled Task Supervision

Production supervision belongs to the frozen deployment in `run_dp/`, not to
this source tree. `run_dp/install.ps1` registers, under `\SEN05\`:

- **SEN05 DP Program Engine** — triggers `AtStartup`, runs
  `run_dp/dp_program.exe` (which reads `live.enabled` / `backfill.enabled` from
  `config.yaml` and forks whichever workflow(s) are on), restart-on-failure.
  A graceful `dp_program stop` exits 0 so it is never fought; only a real crash
  triggers a restart.
- **SEN05 DP Program Watchdog** — runs `run_dp/dp_program.exe --watchdog` on a
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

Install / re-register (from inside the copied `run_dp/` folder, as Administrator):

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1   # goi dp_program.exe --setup
.\dp_program.exe --setup                               # hoac goi thang
```

`--setup` dang ky ca hai task va **go luon task the he cu** (`SEN05 DP Program
Live` / `Backfill`, chay qua `.bat` da bi xoa). Task la trong `\SEN05\` chi duoc
bao cao, khong bao gio bi tu dong xoa.

Full deploy steps: `run_dp/DEPLOY.md`.

Verify registration:

```powershell
Get-ScheduledTask -TaskPath \SEN05\
```

## Validation Before Code Deployment

```powershell
python -m pytest test/
Get-ChildItem src/dp_program -Recurse -Filter *.py | ForEach-Object { python -m py_compile $_.FullName }
python -m dp_program check-sql
python -m dp_program doctor
```

If a validation gate fails, do not start the new runtime. Keep the evidence and
roll back to the last known commit.

## Rollback

Use Git to return to the approved rollback commit, then run validation again.
Do not delete SQL data, truncate staging tables, reboot the host, or rotate
credentials as part of normal rollback.
