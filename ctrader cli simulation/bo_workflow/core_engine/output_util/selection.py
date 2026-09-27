"""selection.py — xếp hạng/lọc nhiều run ĐÃ CHẠY XONG theo tiêu chí tổng quát,
không gắn với riêng phương pháp nào.

Ranh giới có chủ đích [2026-09-22]: mọi thứ CHỈ phục vụ walk-forward (hằng số
`WF_*`, `plateau()`, `pick()`) đã dời sang `optimize/walkforward.py` — file này
giữ đúng phần dùng được cho MỌI phương pháp (quét lưới đơn giản, walk-forward,
hay bất kỳ cách nào khác): `classify()` (phân loại có cấu trúc), `eligible()`
(alias strict, giữ để không đổi hành vi cũ), `rank()` (xếp hạng),
`resolve_profit_factor()`, `ftmo_screen()` (chấm luật FTMO).

[2026-09-25] `eligible()` trước đây gộp 3 câu hỏi khác nhau vào 1 boolean:
(a) trial có chạy hoàn tất không, (b) report/signal có hợp lệ kỹ thuật
không, (c) trial có PHÙ HỢP MỤC ĐÍCH NGHIÊN CỨU không — điểm (c) tự ý coi
margin rejection là lý do loại bỏ tuyệt đối, dù 1 lệnh bị cTrader từ chối vì
thiếu margin vẫn là kết quả THẬT của việc chạy hoàn tất account simulation,
không phải report hỏng. `classify()` tách riêng từng câu hỏi, rồi 2 policy
(`strict_research`/`completed_execution`) tự quyết cách gộp lại."""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from ..models import coerce_float, parse_point_time

# Tên policy CỐ Ý khớp hằng số đã có sẵn trong dashboard/actions.py
# (COMPLETED_EXECUTION_TRIAL_POLICY = "completed_execution_v1") — dashboard
# code đó viết TRƯỚC core này, đang chờ đúng string này xuất hiện ở
# readout.SUPPORTED_TRIAL_POLICIES (qua getattr, xem
# actions.completed_execution_policy_available()). Đổi 2 chuỗi này là phá vỡ
# hợp đồng đã thoả thuận, không chỉ đổi tên nội bộ.
STRICT_RESEARCH_POLICY = "strict_research_v1"
COMPLETED_EXECUTION_POLICY = "completed_execution_v1"
TRIAL_POLICIES = (STRICT_RESEARCH_POLICY, COMPLETED_EXECUTION_POLICY)
_POLICY_FIELDS = {
    STRICT_RESEARCH_POLICY: "strict_research_eligible",
    COMPLETED_EXECUTION_POLICY: "completed_execution_eligible",
}
# Lý do bị loại chỉ THUỘC VỀ margin — lọc ra khi báo cáo loại trừ dưới policy
# completed_execution, vì margin không phải lý do loại dưới policy đó.
_MARGIN_REASONS = frozenset({"margin_rejections", "margin_rejections_unknown"})


def resolve_profit_factor(row: Mapping[str, Any]) -> float:
    pf = coerce_float(row.get("profit_factor"))
    losses = coerce_float(row.get("losing_trades"), 0)
    trades = coerce_float(row.get("total_trades"), 0)
    if trades == 0:
        return math.nan
    if pf == 0 and losses == 0:
        return math.inf
    return pf


def classify(row: Mapping[str, Any], *, min_trades: int = 0) -> dict[str, Any]:
    """Phân loại có cấu trúc cho 1 trial — KHÔNG gộp thành 1 boolean.

    Trả về từng câu hỏi tách biệt (xem docstring module), cộng 2 policy dựng
    sẵn từ các câu hỏi đó:
    - `strict_research_eligible`: HÀNH VI CŨ của `eligible()` — không chấp
      nhận margin rejection dưới bất kỳ hình thức nào (kể cả "unknown").
    - `completed_execution_eligible`: chấp nhận margin rejection MIỄN LÀ
      trial đã chạy hoàn tất (`status=ok`), report/signal/period hợp lệ,
      và đạt `min_trades` — tức "cTrader đã mô phỏng
      xong 1 account, dù account đó có bị từ chối lệnh vì margin". VẪN loại
      nếu margin_rejections là "unknown" (log ghi "trading halted" — bot
      NGỪNG xử lý tín hiệu giữa chừng, khác hẳn "1 lệnh bị từ chối nhưng vẫn
      chạy tiếp hết kỳ") — policy này nới lỏng đúng 1 điều kiện (bao nhiêu
      lệnh bị từ chối margin), không nới lỏng việc phải BIẾT rõ chuyện gì đã
      xảy ra.
    `reasons` liệt kê MỌI lý do một reviewer khó tính (strict) sẽ nêu — dùng
    `exclusion_reasons()` bên dưới để lọc đúng lý do theo TỪNG policy khi báo
    cáo trial bị loại (margin không phải lý do loại dưới completed_execution).
    """
    flags = row.get("validity_flags") or {}
    reasons: list[str] = []

    execution_completed = row.get("status") == "ok"
    if not execution_completed:
        reasons.append("execution_not_completed")

    period_ok = bool(flags.get("period_ok", True))
    if not period_ok:
        reasons.append("period_mismatch")

    signal_ok = bool(flags.get("signal_ok", True))
    if not signal_ok:
        reasons.append("signal_invalid")

    raw_margin = flags.get("margin_rejections", 0)
    margin_unknown = raw_margin in (None, "unknown")
    margin_rejections = None if margin_unknown else int(raw_margin or 0)
    if margin_unknown:
        reasons.append("margin_rejections_unknown")
    elif margin_rejections > 0:
        reasons.append("margin_rejections")

    meets_min_trades = int(row.get("total_trades") or 0) >= min_trades
    if not meets_min_trades:
        reasons.append("below_min_trades")

    # report_valid CỐ Ý không xét margin — margin là kết quả nghiệp vụ của
    # việc mô phỏng account (bị từ chối lệnh THẬT), không phải report hỏng
    # (khác evidence.report_ok(), gộp cả 3 vào 1 — dùng cho mục đích khác:
    # store.py quyết định có giữ log debug hay không, không phải phân loại
    # nghiên cứu — xem module-boundary-principles, KHÔNG đổi evidence.py
    # trong đợt này để không đổi hành vi giữ log).
    report_valid = period_ok and signal_ok
    # "unknown" (log ghi "trading halted") loai duoi CA HAI policy — day la
    # "khong biet chuyen gi da xay ra", khac han "biet ro N lenh bi tu choi".
    base_ok = execution_completed and report_valid and meets_min_trades and not margin_unknown

    return {
        "execution_completed": execution_completed,
        "report_valid": report_valid,
        "period_ok": period_ok,
        "signal_ok": signal_ok,
        "margin_rejections": margin_rejections,
        "meets_min_trades": meets_min_trades,
        "strict_research_eligible": base_ok and margin_rejections == 0,
        "completed_execution_eligible": base_ok,
        "reasons": reasons,
    }


def trial_included(classification: Mapping[str, Any], policy: str) -> bool:
    """Tra ĐÚNG field của 1 classification() đã tính sẵn theo policy — dùng
    khi caller cần đọc CÙNG 1 classification cho nhiều việc (vd đếm margin
    rejection VÀ quyết định nhận/loại) mà không phải gọi `classify()` lại."""
    if policy not in _POLICY_FIELDS:
        raise ValueError(f"unknown trial policy: {policy!r}; supported: {TRIAL_POLICIES}")
    return bool(classification[_POLICY_FIELDS[policy]])


def eligible_under_policy(row: Mapping[str, Any], policy: str, *, min_trades: int = 0) -> bool:
    """Đủ điều kiện theo ĐÚNG 1 policy đã đặt tên — raise nếu policy lạ,
    không âm thầm coi policy sai là "không đủ điều kiện"."""
    return trial_included(classify(row, min_trades=min_trades), policy)


def exclusion_reasons(classification: Mapping[str, Any], policy: str) -> list[str]:
    """Lý do bị loại DƯỚI ĐÚNG policy đó — lọc bỏ lý do margin khi policy là
    completed_execution (margin không phải lý do loại dưới policy này)."""
    if policy not in _POLICY_FIELDS:
        raise ValueError(f"unknown trial policy: {policy!r}; supported: {TRIAL_POLICIES}")
    reasons = list(classification.get("reasons") or [])
    if policy == COMPLETED_EXECUTION_POLICY:
        reasons = [reason for reason in reasons if reason not in _MARGIN_REASONS]
    return reasons


def eligible(row: Mapping[str, Any], *, min_trades: int = 0) -> bool:
    """HÀNH VI CŨ giữ nguyên — alias của `strict_research` policy. Mọi caller
    hiện có (Grid ranking, Walk-forward) tiếp tục strict như trước, không tự
    đổi sang policy khác — xem `rank(..., policy=...)` nếu cần đổi có chủ đích."""
    return classify(row, min_trades=min_trades)["strict_research_eligible"]


def rank(
    rows: Iterable[Mapping[str, Any]],
    *,
    min_trades: int = 0,
    by: tuple[str, ...] = ("profit_factor", "max_equity_drawdown_pct", "net_profit"),
    policy: str = STRICT_RESEARCH_POLICY,
) -> list[dict[str, Any]]:
    values = [dict(row) for row in rows if eligible_under_policy(row, policy, min_trades=min_trades)]
    for row in values:
        row["profit_factor_resolved"] = resolve_profit_factor(row)

    def key(row: Mapping[str, Any]) -> tuple[Any, ...]:
        out = []
        for field in by:
            value = row["profit_factor_resolved"] if field == "profit_factor" else row.get(field)
            if field in {"max_equity_drawdown_pct", "max_balance_drawdown_pct"}:
                out.append(coerce_float(value, math.inf))
            else:
                out.append(-coerce_float(value, -math.inf))
        return tuple(out)

    return sorted(values, key=key)


def ftmo_screen(equity_points: Iterable[Mapping[str, Any]], profile: Mapping[str, Any]) -> dict[str, Any]:
    tz = ZoneInfo(str(profile.get("timezone", "Europe/Prague")))
    start_balance = float(profile["initial_balance"])
    daily_limit = start_balance * float(profile["daily_loss_pct"]) / 100.0
    max_limit = start_balance * float(profile["max_loss_pct"]) / 100.0
    daily_floor: dict[str, float] = {}
    global_min = start_balance
    for point in equity_points:
        timestamp = parse_point_time(point).astimezone(tz)
        day = timestamp.date().isoformat()
        min_equity = float(point.get("minEquity", point.get("equity", point.get("balance"))))
        balance = float(point.get("balance", min_equity))
        daily_floor.setdefault(day, balance)
        global_min = min(global_min, min_equity)
        if daily_floor[day] - min_equity > daily_limit:
            return {"status": "observed_breach", "rule": "daily_loss", "day": day}
    if start_balance - global_min > max_limit:
        return {"status": "observed_breach", "rule": "max_loss"}
    return {"status": "screen_pass"}
