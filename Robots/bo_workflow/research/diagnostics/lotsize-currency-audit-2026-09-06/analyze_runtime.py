import csv
import json
import math
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BATCH = ROOT / "research" / "cli_batches" / "Combo_11sym_1wk_20260904"
OUTPUT = Path(__file__).resolve().parent

GROUPS = {
    "DE40": "DE40",
    "FR40": "FR40",
    "SP35": "SP35",
    "HK50": "HK50",
    "JP225": "J225",
    "US30": "US30",
    "BTCUSD": "BTCUSD",
    "GOLD": "GOLD",
}


def load_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def money_per_point_per_unit(trade: dict) -> float:
    distance = abs(float(trade["closePrice"]) - float(trade["entryPrice"]))
    volume = float(trade["volume"])
    return abs(float(trade["gross"])) / (distance * volume)


evidence = []
for group, folder in GROUPS.items():
    report_path = BATCH / folder / "report.json"
    report = load_report(report_path)
    traded_symbol = report["usedSymbols"][0]
    factors = [
        money_per_point_per_unit(trade)
        for trade in report["history"]["items"]
        if trade["volume"] and trade["entryPrice"] != trade["closePrice"]
    ]
    evidence.append(
        {
            "group": group,
            "symbol": traded_symbol["symbol"],
            "base_asset": traded_symbol["baseAsset"],
            "quote_asset": traded_symbol["quoteAsset"],
            "lot_size_units": float(traded_symbol["lotSize"]),
            "step_volume_units": float(traded_symbol["stepVolume"]),
            "pip_position": int(traded_symbol["pipPosition"]),
            "closed_trades": len(factors),
            "money_per_point_per_unit_median": statistics.median(factors),
            "money_per_point_per_unit_min": min(factors),
            "money_per_point_per_unit_max": max(factors),
            "conversion_symbols": [item["symbol"] for item in report["usedSymbols"][1:]],
            "source_report": str(report_path.relative_to(ROOT)).replace("\\", "/"),
        }
    )

(OUTPUT / "runtime-evidence.json").write_text(
    json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)

risk_money = 500.0
distance = 100.0
core_groups = {"DE40", "HK50", "JP225", "US30", "BTCUSD", "GOLD"}
tests = []
for row in evidence:
    if row["group"] not in core_groups:
        continue
    factor = row["money_per_point_per_unit_median"]
    # USD-quoted symbols are exactly 1 by the cTrader units model. Their tiny
    # empirical deviations come only from gross P/L being stored to cents.
    if row["quote_asset"] == "USD":
        factor = 1.0
    raw_units = risk_money / (distance * factor)
    step = row["step_volume_units"]
    normalized_units = math.floor((raw_units + 1e-12) / step) * step
    tests.append(
        {
            "group": row["group"],
            "risk_money_usd": risk_money,
            "distance_price_points": distance,
            "usd_per_point_per_unit": factor,
            "raw_volume_units": raw_units,
            "normalized_volume_units": normalized_units,
            "lot_size_units": row["lot_size_units"],
            "quantity_lots": normalized_units / row["lot_size_units"],
            "nominal_loss_after_normalize_usd": normalized_units * distance * factor,
        }
    )

with (OUTPUT / "numerical-tests.csv").open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(tests[0]))
    writer.writeheader()
    writer.writerows(tests)

# Same ticks/config, old Symbol.PipValue binary versus the currently deployed
# build-1 PipValueNow binary (without probe scaling). This isolates the HKD
# rounding effect because entry/exit timestamps and prices are identical.
old_hk = load_report(BATCH / "HK50" / "report.json")
new_hk_path = ROOT / "research" / "cli_runs" / "Combo_HK50.cash_h1_20260906-160154" / "report.json"
new_hk = load_report(new_hk_path)
comparisons = []
for old_trade, new_trade in zip(old_hk["history"]["items"], new_hk["history"]["items"]):
    comparisons.append(
        {
            "entry_time_ms": old_trade["entryTime"],
            "direction": old_trade["direction"],
            "entry_price": old_trade["entryPrice"],
            "close_price": old_trade["closePrice"],
            "old_volume_units": old_trade["volume"],
            "build1_volume_units": new_trade["volume"],
            "volume_ratio": new_trade["volume"] / old_trade["volume"],
            "old_gross_usd": old_trade["gross"],
            "build1_gross_usd": new_trade["gross"],
        }
    )

with (OUTPUT / "hk50-build1-comparison.csv").open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(comparisons[0]))
    writer.writeheader()
    writer.writerows(comparisons)

print(json.dumps({"runtime": evidence, "numerical_tests": tests, "hk50": comparisons}, indent=2))
