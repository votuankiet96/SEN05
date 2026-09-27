from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    api_root = Path(__file__).resolve().parents[1]
    robots_root = api_root.parents[1]
    sys.path.insert(0, str(robots_root))

    from bo_workflow.api_engine import BacktestApiJob, BridgeClient

    signal_dir = Path(os.environ.get(
        "BO_SIGNAL_EXPORT_DIR",
        r"Z:\Desktop\og_program\runtime\exports",
    ))

    client = BridgeClient(api_root / "bridge")
    job_id = client.new_job_id("smoke-us30-h1-m1")
    job = BacktestApiJob(
        job_id=job_id,
        robot_name="Combo",
        algo_path=str(robots_root / "Combo.algo"),
        symbol="US30.cash",
        timeframe="h1",
        start_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_utc=datetime(2026, 1, 8, tzinfo=timezone.utc),
        balance=100_000,
        data_mode="m1",
        precise_conversion=False,
        commission_auto=False,
        parameters={
            "SignalFilePath": str(signal_dir / "combo_US30_H1_full_history_signals.csv"),
            "KslLevel": "Fib1000",
            "KtpLevel": "Fib2618",
            "RiskPercent": "0.5",
            "MaxMarginPercent": "50",
            "EnableDailyLossLimit": "False",
            "MaxDailyLossPercent": "5",
            "EnableMaxDrawdown": "False",
            "MaxTotalDrawdownPercent": "10",
            "EnableMaxConsecutiveLosses": "False",
            "MaxConsecutiveLosses": "5",
        },
    )

    print(f"job_id={job_id}")
    print(f"job_file={client.submit(job)}")
    result = client.wait_result(job_id, timeout_seconds=300, poll_seconds=1)
    print(f"status={result.status}")
    print(f"backtesting_error={result.backtesting_error}")
    print(f"wall_seconds={result.wall_seconds}")

    if result.json_report:
        report = json.loads(result.json_report)
        main = report.get("main", {})
        equity = report.get("equity", {})
        trades = report.get("tradeStatistics", {}).get("totalTrades", {}).get("all")
        print(f"net_profit={main.get('netProfit')}")
        print(f"max_equity_drawdown_percent={equity.get('maxEquityDrawdownPercent')}")
        print(f"trades={trades}")


if __name__ == "__main__":
    main()
