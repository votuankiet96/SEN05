# 24/7 Operations

Use these wrappers for production-style scheduling on Windows:

- `powershell -ExecutionPolicy Bypass -File ops/run_ws_live_forever.ps1`
- `powershell -ExecutionPolicy Bypass -File ops/run_signal_watcher_forever.ps1`
- `powershell -ExecutionPolicy Bypass -File ops/run_pipeline_daily.ps1`
- `powershell -ExecutionPolicy Bypass -File ops/run_checker_cycle.ps1`

Signal watcher Task Scheduler install:

- Install/start hidden background watcher:
  `powershell -ExecutionPolicy Bypass -File ops/install_signal_watcher_task.ps1 -StartNow`
- Check watcher status:
  `powershell -ExecutionPolicy Bypass -File ops/status_signal_watcher.ps1`

Recommended Task Scheduler layout:

- `ws_live`: start at boot or user logon, action = `ops/run_ws_live_forever.ps1`
- `signal_watcher`: start at user logon, action = `ops/run_signal_watcher_forever.ps1`
- `pipeline`: daily one-shot, action = `ops/run_pipeline_daily.ps1`
- `checker`: every 3 days one-shot, action = `ops/run_checker_cycle.ps1`

Recommended checks before trusting the system:

- Verify `data_provider/logs/ws_live.log`, `pipeline.log`, `checker.log` are updating
- Verify `logs/watcher.log` and `logs/watcher_supervisor.log` are updating
- Verify `SEN.ActiveTask` does not show stuck expired locks
- Verify derived TF bad tick rows stay at `0`
