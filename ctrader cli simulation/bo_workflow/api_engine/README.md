# api_engine — ENGINE #2 (Plugin cTrader Desktop qua bridge)

Đổi tên từ `ctrader_api/` 2026-09-17 khi tách hệ backtest thành 1 pipeline
chung (`core_engine/`, xem `core_engine/facilitator.py`) + 2 engine thực thi
ngang hàng: `cli_engine/` (spawn `ctrader-cli.exe`) và **`api_engine/`** (folder
này — Plugin native `Backtesting.Start()` qua file-mailbox).

Không còn "độc lập với pipeline" như README cũ ghi — điểm nối chính thức là
`api_runner.py`, được `core_engine/facilitator.py` gọi vào khi
`EngineProfile.backend == "api"` trong `core_engine/config.yaml`. Code Python
(`contracts.py`, `bridge_client.py`) đã dời thẳng vào đây (trước ở
`ctrader_api/python/`) — cả 2 engine giờ đều là code Python "hạng nhất", không
còn ai là "spike thử nghiệm" nữa.

## Layout

```text
api_engine/
  api_runner.py     Điểm nối DUY NHẤT với core_engine/facilitator.py — build
                     job từ RunSpec, gọi bridge_client, dịch kết quả ngược lại
                     thành core_engine.models.RunResult.
  contracts.py       Schema job/result (BacktestApiJob/BacktestApiResult).
  bridge_client.py   Ghi job, chờ kết quả qua bridge/.
  bridge/
    jobs/       Python writes `<job_id>.json`
    claimed/    plugin atomically moves jobs here before execution
    results/    plugin writes `<job_id>.json` on success/failure
    failed/     plugin writes malformed/claim-time failures here
    heartbeat/  plugin liveness marker
  plugin/
    BoBacktestRunner/
      BoBacktestRunner/
        BoBacktestRunner.cs
        BacktestMapper.cs
        Contracts.cs
        BoBacktestRunner.csproj
        config.json
        GlobalUsings.cs
      BoBacktestRunner.sln
  scripts/
    deploy_plugin.py
    smoke_combo.py
```

Test: `bo_workflow/tests/test_api_engine.py` (dời từ `ctrader_api/tests/`,
gộp thêm test cho `api_runner.py`).

## Current scope

This bridge is usable only inside a certified engine profile. It deliberately
fails closed when the Desktop API cannot represent a requested GUI setting.

- Nó không import/sửa `cli_engine/` (2 engine không gọi lẫn nhau).
- It does not modify `Combo/`, `MA Cross/`, or any `.algo`.
- It uses the cTrader Plugin Backtesting API: `Backtesting.Start(...)`.
- It is designed to answer the hard questions first: `RobotType` discovery,
  parameter ordering, `PreciseConversion`, and real Desktop concurrency.
- The local cTrader Plugin API 5.9.16 exposes neither
  `BacktestingSettings.PreciseConversion` nor
  `ApplyCommissionAutomatically`. Requests for either setting return
  `status=unsupported_capability`; they are never silently replaced by
  approximate conversion or zero commission.
- Each heartbeat and result contains the runtime capability matrix.
- Unknown parameter names, invalid enum ordinals and mismatched report identity
  return an error instead of a plausible but incorrect report.
- `tick-csv` is rejected on this runtime. It is never mapped to server ticks.

## Build

The source in this folder is canonical. cTrader Desktop loads editable plugin
projects from `Documents\cAlgo\Sources\Plugins`, so deploy the source mirror
before using it in the UI:

```powershell
python bo_workflow\api_engine\scripts\deploy_plugin.py
```

Manual build:

```powershell
& "$env:LOCALAPPDATA\Spotware\cTrader\abb70432efbee65d18af69e79fe8efe1\ctrader-cli.exe" `
  build "C:\Users\Administrator\Documents\cAlgo\Sources\Robots\bo_workflow\api_engine\plugin\BoBacktestRunner\BoBacktestRunner\BoBacktestRunner.csproj"
```

On this machine, cTrader CLI writes the build artifact to:

```text
C:\Users\Administrator\Documents\cAlgo\Sources\Plugins\BoBacktestRunner.algo
```

The produced `.algo` must be installed/enabled in cTrader Desktop. The plugin
needs file-system access because it reads/writes the bridge folder.
The current default concurrency is `MaxConcurrentJobs=12`.

**[2026-09-17] Đổi tên `ctrader_api/` → `api_engine/`** — nếu plugin đang chạy
sống từ trước khi đổi (kể cả bản đang nạp sẵn trong cTrader.exe lúc đổi tên),
`config.json` mới không tự áp dụng cho phiên đang mở — phải cập nhật tay
Parameter `BridgeRoot` trong Automate UI thành:

```text
C:\Users\Administrator\Documents\cAlgo\Sources\Robots\bo_workflow\api_engine\bridge
```

rồi khởi động lại phiên backtest (hoặc restart cTrader Desktop) để plugin đọc
lại default mới trong `config.json`.

## Smoke test

After enabling the plugin in cTrader Desktop, verify the bridge:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python bo_workflow\api_engine\scripts\smoke_combo.py
```

The first successful smoke on this machine was:

```text
US30.cash h1, 2026-01-01 → 2026-01-08, m1
status=ok, wall_seconds=24.857, net_profit=2740.96, trades=4
```

## Job contract

Python writes JSON with schema `bo-backtest-api-job/v2`. Dates are UTC ISO-8601
timestamps. `preciseConversion` and `commissionAuto` are mandatory, explicit
booleans. `endUtc` is exact; do not apply the CLI `end + 1 day` workaround.

See `contracts.py` for the canonical fields.

## Certification gate

Certification is per engine profile, not per backend name. Compare a manually
created GUI golden against the API report at trade level (timestamp, side,
entry, exit, volume, gross/net) and compare the resolved environment settings.

On the installed 5.9.16 runtime, JP225 with GUI precise conversion disabled and
manual zero commission is certified for the tested fixture: all 14 history rows
and all strategy/equity metrics matched. Contract v2 uses enum names because
this API build does not expose enum domains. GUI precise conversion or automatic
commission is not certifiable because the local Plugin API cannot express those
settings. See `../reports/ctrader-api-gui-parity-debug-2026-09-12.md`.
