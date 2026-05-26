# SEN05 Ops

This folder is the operator layer for local engineering and later automation.
It should call stable data-provider entrypoints through `lib/Sen05Ops.psm1`
instead of reaching into data-provider internals.

## Interactive app

```powershell
powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1
```

The app includes:

- Pipeline: dry-run, gap, full, scoped run, scoped reset/reload.
- WS Live: foreground run or supervised forever loop.
- Checker / Repair: dry-run scans, C-O check, TF gap check, computed TF rebuild.
- Data Dashboard: starts `data_provider/03_chart.py`.
- Probe / Diagnostics: runs TradingView WS history-depth probes.
- Logs / Status: quick process and log inspection.

## Non-interactive examples

```powershell
powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1 -Command pipeline -Mode gap
powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1 -Command pipeline -Mode auto -DryRun
powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1 -Command checker -DryRun
powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1 -Command checker -TfCheck -DryRun
powershell -ExecutionPolicy Bypass -File ops/data_provider_app.ps1 -Command ws-live -Forever
powershell -ExecutionPolicy Bypass -File ops/run_data_chart.ps1
```

Use `-Yes` only for non-interactive execution of write-capable commands that
already have external safeguards.

## Files intentionally kept

- `open_chart.ps1` and `open_chart.bat`: core Python chart launcher.
- `run_combo_chart.ps1`: combo strategy chart launcher.
