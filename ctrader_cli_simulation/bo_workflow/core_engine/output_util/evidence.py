from __future__ import annotations

import gzip
import json
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Mapping

from ..models import RunSpec

# Khớp đủ field pipeline/core/metrics.py từng có (đối chiếu 2026-09-12) — thiếu
# field nào thì đó là chỉ số người dùng không xem lại được từ report đã nén.
MAIN_FIELDS = {
    "netProfit": "net_profit",
    "roi": "roi",
    "startingCapital": "starting_capital",
    "endingBalance": "ending_balance",
    "endingEquity": "ending_equity",
    "accountType": "account_type",
    "accountLeverage": "account_leverage",
}
EQUITY_FIELDS = {
    "maxEquityDrawdownPercent": "max_equity_drawdown_pct",
    "maxEquityDrawdownAbsolute": "max_equity_drawdown_abs",
    "maxBalanceDrawdownPercent": "max_balance_drawdown_pct",
    "maxBalanceDrawdownAbsolute": "max_balance_drawdown_abs",
}
TRADE_FIELDS = {
    "profitFactor": "profit_factor",
    "totalTrades": "total_trades",
    "winningTrades": "winning_trades",
    "losingTrades": "losing_trades",
    "maxConsecutiveWinningTrades": "max_consec_wins",
    "maxConsecutiveLosingTrades": "max_consec_losses",
    "largestWinningTrade": "largest_win",
    "largestLosingTrade": "largest_loss",
    "averageTrade": "average_trade",
    "commissions": "commissions",
    "swaps": "swaps",
}


def read_report(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def _all(value: Any) -> Any:
    return value.get("all") if isinstance(value, dict) else value


def metrics(report: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for source, table in (
        (report.get("main", {}), MAIN_FIELDS),
        (report.get("equity", {}), EQUITY_FIELDS),
        (report.get("tradeStatistics", {}), TRADE_FIELDS),
    ):
        for key, target in table.items():
            out[target] = _all(source.get(key))
    out["period_formatted"] = ((report.get("main") or {}).get("testingPeriod") or {}).get("formatted")
    total = out.get("total_trades") or 0
    wins = out.get("winning_trades") or 0
    out["win_rate"] = round(wins / total, 4) if total else None
    return out


def environment(report: Mapping[str, Any]) -> dict[str, Any]:
    main = dict(report.get("main", {}))
    return {
        "account_type": main.get("accountType"),
        "account_leverage": main.get("accountLeverage"),
        "deposit_asset": main.get("depositAsset"),
        "used_symbols": report.get("usedSymbols", []),
    }


# Mot lenh bi choi sinh HAI dong log khac nhau, cung mot moc thoi gian toi
# mili-giay: mot cua engine (`Trade | ... FAILED with error "NOT_ENOUGH_
# MARGIN_BALANCE"`) va mot cua chinh cBot (`Info | ... was rejected: NoMoney.`).
# Dem so DONG vi vay ra gap doi so LENH that — do 2026-09-12 tren MA Cross
# US30 m30 Q2-2026: 14 dong / 7 lenh. Gom theo moc thoi gian dau dong thi moi
# su kien chi tinh 1 lan, ke ca khi chi co mot trong hai dong xuat hien.
_REJECTION_LINE = re.compile(r"margin|not enough money|rejected", re.I)
_LOG_TIMESTAMP = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}\.\d+)")


def _count_rejections(text: str) -> int:
    stamped: set[str] = set()
    unstamped = 0
    for line in text.splitlines():
        if not _REJECTION_LINE.search(line):
            continue
        match = _LOG_TIMESTAMP.match(line.strip())
        if match:
            stamped.add(match.group(1))
        else:
            unstamped += 1
    return len(stamped) + unstamped


def bot_summary(text: str) -> dict[str, int | None]:
    summary = {
        "loaded": None,
        "processed": None,
        "before_start": None,
        "not_processed": None,
        "placed": None,
        "failed": None,
        "guard_skipped": None,
        "pending_expired": None,
        "same_direction_skipped": None,
        "reversed": None,
        "filled": len(re.findall(r"\bFILLED\b", text)),
        "reversal_cancels": len(re.findall(r"reversal - cancelling existing", text, re.I)),
        "margin_rejections": _count_rejections(text),
    }
    patterns = {
        "loaded": r"loaded=(\d+)",
        "processed": r"processed=(\d+)",
        "before_start": r"before-start=(\d+)",
        "not_processed": r"not-processed=(\d+)",
        "placed": r"placed=(\d+)",
        "failed": r"failed=(\d+)",
        "guard_skipped": r"guard-skipped=(\d+)",
        "pending_expired": r"pending-expired=(\d+)",
        "same_direction_skipped": r"same-direction-skipped=(\d+)",
        "reversed": r"reversed=(\d+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            summary[key] = int(match.group(1))
    if "trading halted" in text.lower() and not summary["margin_rejections"]:
        summary["margin_rejections"] = None
    return summary


def validity_flags(
    spec: RunSpec,
    report: Mapping[str, Any] | None,
    summary: Mapping[str, int | None],
) -> dict[str, Any]:
    flags: dict[str, Any] = {}
    if not report:
        flags.update(period_ok=False, signal_ok=False, approx_fx=None)
        return flags
    period = (report.get("main") or {}).get("testingPeriod") or {}
    actual_start = _ms_date(period.get("startDate"))
    actual_end = _ms_date(period.get("endDate"))
    expected_end = spec.end.isoformat()   # spec.end la moc dung (exclusive)
    flags["period_ok"] = (
        actual_start == spec.start.isoformat()
        and actual_end == expected_end
    )
    flags["signal_ok"] = (summary.get("loaded") or 0) > 0 and (summary.get("processed") or 0) > 0
    env = environment(report)
    deposit = env.get("deposit_asset")
    flags["approx_fx"] = any(
        item.get("quoteAsset") and deposit and item.get("quoteAsset") != deposit
        for item in env.get("used_symbols") or []
    )
    flags["margin_rejections"] = summary.get("margin_rejections")
    return flags


def report_ok(flags: Mapping[str, Any]) -> bool:
    """3 điều kiện "báo cáo có đáng tin không": đúng khung ngày, tín hiệu nạp
    được, không bị từ chối margin. Dời về đây 2026-09-21 — trước đó store.py
    (`_has_bad_flag`) và selection.py (`eligible`) mỗi nơi tự chép lại y hệt 3
    dòng này, đã lệch nhau sau vài lần sửa. `status`/`min_trades` KHÔNG gộp
    vào đây — đó là câu hỏi khác (phù hợp mục đích nghiên cứu, không phải
    "report có kỹ thuật ổn không"; xem selection.classify())."""
    if not flags.get("period_ok", True):
        return False
    if not flags.get("signal_ok", True):
        return False
    value = flags.get("margin_rejections", 0)
    if value in (None, "unknown"):
        return False
    return int(value or 0) == 0


def _ms_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(float(value) / 1000, timezone.utc).date().isoformat()


# --------------------------------------------------------------------------- #
# 2 hàm dưới đây là CÔNG CỤ TAY để đối chiếu 1 run CLI với report GUI cùng
# tham số — người dùng tự đối chiếu khi chạy, và nhờ kiểm tra khi thấy lệch.
# Kết quả đối chiếu không được ghi lại ở đâu và không chặn/lọc run nào.
# --------------------------------------------------------------------------- #


def read_gui_report(report_html: str | Path) -> dict[str, Any]:
    text = Path(report_html).read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r"<script[^>]+id=[\"']backtesting-report[\"'][^>]*>(.*?)</script>",
        text,
        re.S | re.I,
    )
    if not match:
        raise ValueError("GUI report.html does not contain backtesting-report JSON")
    return json.loads(unescape(match.group(1)).strip())


def compare_history(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    a = (left.get("history") or {}).get("items") or []
    b = (right.get("history") or {}).get("items") or []
    mismatches = []
    for index, (row_a, row_b) in enumerate(zip(a, b), start=1):
        if row_a != row_b:
            mismatches.append({"index": index, "left": row_a, "right": row_b})
            if len(mismatches) >= 10:
                break
    return {
        "left_count": len(a),
        "right_count": len(b),
        "matched": len(a) == len(b) and not mismatches,
        "mismatches": mismatches,
    }
