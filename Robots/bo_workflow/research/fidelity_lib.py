"""Logic dùng chung cho việc đối chiếu signal CSV <-> log backtest.

Tách ra từ signal_fidelity_check.ipynb (đã tự-kiểm-chứng khớp với số liệu
thật, xem AGENT.md 2026-09-01) để signal_chart_visualizer.ipynb dùng lại
đúng logic đã validate, không viết lại lần 2 (tránh lệch kết quả giữa 2
notebook). signal_fidelity_check.ipynb giữ nguyên bản sao độc lập của mình,
không phụ thuộc file này — không rebuild lại notebook đó để tránh rủi ro.
"""
import re
import json
from pathlib import Path

import pandas as pd

# "alignment=X, " la nhom KHONG BAT BUOC trong ca 3 regex duoi day - ban
# Combo.cs tu 2026-09-01 (bo han co che exact-match/missing-bar-fallback,
# xem AGENT.md) khong con in field nay nua; ban MA Cross.cs va cac ban Combo
# cu hon van con. Giu tuong thich ca 2, cot "alignment" se la None cho log moi.
_ALIGNMENT = r"(?:alignment=(?P<alignment>\w+), )?"

PLACED_RE = re.compile(
    r"bartime=(?P<bartime>\S+ \S+), " + _ALIGNMENT + r"executed=(?P<executed>\S+ \S+), "
    r"(?:pending|market) (?P<direction>Buy|Sell) placed(?: at (?P<entry>[\d.]+))?;"
    # SL/TP nam ngay sau dau ';' cua chinh dong "placed" nay (Combo: gia
    # tuyet doi; MA Cross: pips, co hau to " pips"). Boc trong 1 nhom KHONG
    # BAT BUOC de khong lam gay match cu neu sau nay wording doi khac.
    r"(?: SL=(?P<sl>[\d.]+)(?P<sl_pips> pips)?, TP=(?P<tp>[\d.]+)(?P<tp_pips> pips)?)?"
)
REJECTED_RE = re.compile(
    r"bartime=(?P<bartime>\S+ \S+), " + _ALIGNMENT +
    r"(?:pending \S+ at [\d.]+ |market \S+ )was rejected: (?P<error>\S+)\."
)
# Chi co the xuat hien tren log ban Combo.cs TRUOC 2026-09-01 (co co che
# exact-match/missing-bar-fallback) - ban hien tai da bo han co che nay
# (nguoi dung quyet dinh: bot chi phan ung theo thoi gian thuc, khong con
# khai niem "khop dung 1 nen FTMO" nua), nen dong nay se KHONG BAO GIO xuat
# hien trong log moi - giu lai regex chi de doc lai log cu neu can.
FALLBACK_EXPIRED_RE = re.compile(
    r"bartime=(?P<bartime>\S+ \S+) has no exact FTMO bar and expired at (?P<expired_at>\S+ \S+) "
    r"before a tradable tick arrived\."
)
# Chi co tu ban Combo.cs sau 2026-09-01 (ReconcileExistingExposure) - cac ban
# log cu hon (truoc khi them dong Print() nay) khong co dong nay, xem
# SUMMARY_RE + xu ly fallback trong build_fidelity_report().
SAME_DIRECTION_SKIP_RE = re.compile(
    r"bartime=(?P<bartime>\S+ \S+), " + _ALIGNMENT +
    r"(?P<direction>Buy|Sell) signal skipped: already have same-direction exposure open\."
)
# Dong tong ket duy nhat cuoi log (OnStop) - dung de doi chieu so luong khi
# 1 loai outcome khong co dong log rieng tung tin hieu. 2 dinh dang khac nhau
# theo thoi gian (xem AGENT.md 2026-09-01):
#   - Ban MOI (da bo exact-match/fallback): khong con "exact"/"fallback"/
#     "fallback-expired" rieng, gop chung thanh "processed".
#   - Ban CU: co du "exact"/"fallback"/"fallback-expired".
# parse_summary_counters() thu ban MOI truoc, khong khop moi thu ban CU.
_SUMMARY_RE_NEW = re.compile(
    r"loaded=(?P<loaded>\d+), processed=(?P<processed>\d+), before-start=(?P<before_start>\d+), "
    r"not-processed=(?P<not_processed>\d+), placed=(?P<placed>\d+), failed=(?P<failed>\d+), "
    r"guard-skipped=(?P<guard_skipped>\d+), pending-expired=(?P<pending_expired>\d+), "
    r"same-direction-skipped=(?P<same_direction_skipped>\d+), reversed=(?P<reversed>\d+)\."
)
_SUMMARY_RE_OLD = re.compile(
    r"loaded=(?P<loaded>\d+), exact=(?P<exact>\d+), fallback=(?P<fallback>\d+), "
    r"fallback-expired=(?P<fallback_expired>\d+), before-start=(?P<before_start>\d+), "
    r"not-processed=(?P<not_processed>\d+), placed=(?P<placed>\d+), failed=(?P<failed>\d+), "
    r"guard-skipped=(?P<guard_skipped>\d+), pending-expired=(?P<pending_expired>\d+)"
    r"(?:, same-direction-skipped=(?P<same_direction_skipped>\d+), reversed=(?P<reversed>\d+))?\."
)
VOLUME_FLOOR_RE = re.compile(r"risk calculates to [\d.]+ units, below broker minimum")


def parse_summary_counters(log_path: str) -> dict[str, int | None]:
    """Doc dong tong ket duy nhat cuoi log.txt (OnStop) thanh dict int. Tu
    nhan dien dinh dang moi (co 'processed=') hay cu (co 'exact=') - xem
    _SUMMARY_RE_NEW/_SUMMARY_RE_OLD. Key khong co trong dinh dang dang doc
    tra ve None thay vi KeyError - luon kiem tra None truoc khi dung."""
    text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    match = _SUMMARY_RE_NEW.search(text) or _SUMMARY_RE_OLD.search(text)
    if not match:
        return {}
    return {key: (int(value) if value is not None else None) for key, value in match.groupdict().items()}


def load_signal_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["bartime"] = pd.to_datetime(df["bartime"])
    return df


def parse_log(log_path: str) -> tuple[pd.DataFrame, int]:
    text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    records: list[dict] = []
    for m in PLACED_RE.finditer(text):
        records.append({
            "bartime": m.group("bartime"), "alignment": m.group("alignment"),
            "outcome": "placed", "log_direction": m.group("direction"),
            "log_entry": m.group("entry"), "error": None,
            # Thoi diem THUC SU goi lenh ra broker - khac "bartime" (nen CSV
            # sinh signal) it nhat 1 nen (OnBarClosed chi bao khi nen dong),
            # va co the khac NHIEU nen neu la fallback. Day la nen "vao lenh"
            # that su, dung de ve marker rieng - xem build_markers() ben
            # signal_chart_visualizer.ipynb.
            "executed": m.group("executed"),
            "sl": m.group("sl"), "tp": m.group("tp"),
            # Combo: gia tuyet doi (khong co hau to). MA Cross: khoang cach
            # pips (co hau to " pips") - khong co gia entry tuyet doi vi la
            # market order, "dat vao gia thi truong" tai thoi diem khop.
            "sl_is_pips": m.group("sl_pips") is not None,
        })
    for m in REJECTED_RE.finditer(text):
        records.append({
            "bartime": m.group("bartime"), "alignment": m.group("alignment"),
            "outcome": "rejected", "log_direction": None,
            "log_entry": None, "error": m.group("error"), "executed": None,
            "sl": None, "tp": None, "sl_is_pips": None,
        })
    for m in FALLBACK_EXPIRED_RE.finditer(text):
        records.append({
            "bartime": m.group("bartime"), "alignment": "FallbackAligned",
            "outcome": "fallback_expired_waiting", "log_direction": None,
            "log_entry": None, "error": None, "executed": None,
            "sl": None, "tp": None, "sl_is_pips": None,
        })
    for m in SAME_DIRECTION_SKIP_RE.finditer(text):
        records.append({
            "bartime": m.group("bartime"), "alignment": m.group("alignment"),
            "outcome": "same_direction_skipped", "log_direction": m.group("direction"),
            "log_entry": None, "error": None, "executed": None,
            "sl": None, "tp": None, "sl_is_pips": None,
        })
    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df["bartime"] = pd.to_datetime(df["bartime"])
        df["executed"] = pd.to_datetime(df["executed"])
        df["sl"] = pd.to_numeric(df["sl"], errors="coerce")
        df["tp"] = pd.to_numeric(df["tp"], errors="coerce")
    n_untraceable = len(VOLUME_FLOOR_RE.findall(text))
    return df, n_untraceable


def get_run_window(run_dir: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    events = json.loads((Path(run_dir) / "events.json").read_text(encoding="utf-8"))
    times = sorted(e["time"] for e in events if e.get("time") is not None)
    if not times:
        return None, None
    start = pd.to_datetime(times[0], unit="ms", utc=True).tz_localize(None)
    end = pd.to_datetime(times[-1], unit="ms", utc=True).tz_localize(None)
    return start, end


def get_fill_outcomes(run_dir: str) -> pd.DataFrame:
    """Chỉ áp dụng cho Combo (Pending Stop) — truy vết mỗi 'Create Stop Order'
    (khớp orderId) xem cuối cùng Filled (thành vị thế thật) hay Cancelled
    (hết hạn không khớp), hoặc còn treo cuối kỳ test.

    Trả về DataFrame [bartime, fill_status] để merge thêm vào report chính,
    tách rõ 'placed' (broker chấp nhận đặt) khỏi 'filled' (thực sự thành
    giao dịch) — xem thảo luận 2026-09-01 trong lịch sử hội thoại.
    """
    events = json.loads((Path(run_dir) / "events.json").read_text(encoding="utf-8"))
    creates = {e["orderId"]: e for e in events if e.get("event") == "Create Stop Order"}
    filled_order_ids = {e["orderId"] for e in events if e.get("event") == "Stop Order Filled"}
    cancelled_order_ids = {e["orderId"] for e in events if e.get("event") == "Order cancelled"}

    rows = []
    for oid, create_evt in creates.items():
        if oid in filled_order_ids:
            fill_status = "filled"
        elif oid in cancelled_order_ids:
            fill_status = "expired_unfilled"
        else:
            fill_status = "still_pending_at_end"
        rows.append({"entryPrice": create_evt.get("entryPrice"), "fill_status": fill_status})
    return pd.DataFrame(rows)


_CLOSE_EVENT_REASON = {
    "Stop Loss Hit": "sl",
    "Take Profit Hit": "tp",
    "Position closed": "other",  # force-close cuoi ky test / Position Management, khong phai SL/TP
}


def get_exit_events(run_dir: str) -> pd.DataFrame:
    """Moi lenh DONG that su (Stop Loss Hit / Take Profit Hit / Position
    closed - 3 event nay LUON cong lai dung bang so 'Create Position', xem
    AGENT.md 2026-09-01: da doi chieu US30 154+69+1=224, MA Cross 247+95+1=343)
    - dung de ve marker "diem thoat lenh" rieng tren chart, doc lap voi
    marker Signal/Entry da co (yeu cau nguoi dung 2026-09-01: "muon theo doi
    dinh SL/TP o dau tren chart" sau khi audit 1 case cu the).

    Doc THANG tu events.json, KHONG can merge/doi chieu voi CSV signal - moi
    close event da tu mang du entryPrice/closePrice/grossProfit/balance/type
    (Buy|Sell cua CHINH position do). Thoi diem MO lenh (entryTime) tra cuu
    them qua positionId tu chinh event 'Create Position' de tinh duoc thoi
    gian giu lenh.

    Tra ve DataFrame [time, reason(sl/tp/other), direction(Buy/Sell),
    entryPrice, closePrice, grossProfit, pips, balance, entryTime] - 'time'
    la pd.Timestamp UTC-naive (khop convention bartime/executed da dung xuyen
    suot file nay).
    """
    events = json.loads((Path(run_dir) / "events.json").read_text(encoding="utf-8"))
    entry_time_by_position = {
        e["positionId"]: e["time"] for e in events if e.get("event") == "Create Position"
    }
    rows = []
    for e in events:
        reason = _CLOSE_EVENT_REASON.get(e.get("event"))
        if reason is None:
            continue
        entry_ms = entry_time_by_position.get(e.get("positionId"))
        rows.append({
            "time": e["time"], "reason": reason, "direction": e.get("type"),
            "entryPrice": e.get("entryPrice"), "closePrice": e.get("closePrice"),
            "grossProfit": e.get("grossProfit"), "pips": e.get("pips"),
            "balance": e.get("balance"),
            "entryTime": entry_ms,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True).dt.tz_localize(None)
        df["entryTime"] = pd.to_datetime(df["entryTime"], unit="ms", utc=True).dt.tz_localize(None)
    return df


def get_equity_curve(run_dir: str) -> list[dict]:
    """Duong so du tai khoan theo TUNG lenh dong (Stop Loss Hit/Take Profit
    Hit/Position closed - moi event nay co san field 'balance'/'equity' khong
    null trong events.json). Day la duong dang STEP (doi dung luc dong lenh),
    KHONG PHAI equity tuc thoi theo tick - events.json khong ghi lai equity
    lien tuc, chi ghi tai cac moc dong lenh.

    Diem dau tien: suy nguoc balance khoi diem tu chinh lenh dong dau tien
    (balance - grossProfit), gan tai thoi diem event DAU TIEN cua ca run (moc
    "tai khoan bat dau") de duong ve co doan phang truoc giao dich dau, khop
    truc thoi gian voi chart nen ben canh.

    Tra ve list[{"time": <unix-seconds-int>, "value": <balance>}], da sap
    theo thoi gian - dung thang cho setData() cua 1 Line/Area series.
    """
    events = json.loads((Path(run_dir) / "events.json").read_text(encoding="utf-8"))
    all_times = sorted(e["time"] for e in events if e.get("time") is not None)
    closes = sorted((e for e in events if e.get("balance") is not None), key=lambda e: e["time"])
    if not all_times or not closes:
        return []

    initial_balance = closes[0]["balance"] - (closes[0].get("grossProfit") or 0)
    points = [{"time": all_times[0] // 1000, "value": round(initial_balance, 2)}]
    for e in closes:
        points.append({"time": e["time"] // 1000, "value": round(e["balance"], 2)})
    return points


def build_fidelity_report(signal_csv_path: str, archived_run_dir: str, strategy: str):
    run_dir = Path(archived_run_dir)
    signals = load_signal_csv(signal_csv_path)
    log_events, n_untraceable = parse_log(str(run_dir / "log.txt"))
    start, end = get_run_window(run_dir)

    params_path = run_dir / "parameters.cbotset"
    fallback_enabled = True
    if params_path.exists():
        params = json.loads(params_path.read_text(encoding="utf-8"))
        raw = params.get("Parameters", {}).get("EnableMissingBarFallback")
        if raw is not None:
            fallback_enabled = str(raw).strip().lower() in ("true", "1")

    merged = signals.merge(log_events, on="bartime", how="left")
    merged["status"] = merged["outcome"].fillna("not_found_in_log")

    if start is not None:
        in_window = (merged["bartime"] >= start) & (merged["bartime"] <= end)
        still_missing = merged["status"] == "not_found_in_log"
        merged.loc[~in_window & still_missing, "status"] = "before_test_window"
        if fallback_enabled:
            merged.loc[in_window & still_missing, "status"] = "⚠ IN_WINDOW_BUT_MISSING"
        else:
            merged.loc[in_window & still_missing, "status"] = "in_window_no_exact_bar_fallback_off"

    if strategy == "combo":
        # Cac ban log Combo TRUOC khi them dong Print() rieng cho
        # same-direction-skip (2026-09-01, xem AGENT.md) khong co dong log
        # tung tin hieu cho case nay - chung roi vao "⚠ IN_WINDOW_BUT_MISSING"
        # oan uong. Doi chieu qua dong tong ket OnStop: neu SO LUONG dong "⚠"
        # khop CHINH XAC voi counter same-direction-skipped that (khong doan
        # mo qua gia hay bat ky khoa nao khac), gan lai nhan chinh xac hon.
        # Neu KHONG khop, GIU NGUYEN canh bao "⚠" - uu tien khong che giau
        # bat thuong that hon la lam gon giao dien.
        summary = parse_summary_counters(str(run_dir / "log.txt"))
        expected = summary.get("same_direction_skipped")
        anomaly_mask = merged["status"] == "⚠ IN_WINDOW_BUT_MISSING"
        anomaly_count = int(anomaly_mask.sum())
        if expected is not None and expected > 0 and anomaly_count == expected:
            merged.loc[anomaly_mask, "status"] = "same_direction_skipped_inferred"

    def _check_direction(row):
        if row["status"] != "placed":
            return None
        expect = "Buy" if row["signal"] == 1 else "Sell"
        return expect == row["log_direction"]

    merged["direction_ok"] = merged.apply(_check_direction, axis=1)

    if strategy == "combo" and "entry" in merged.columns:
        def _check_entry(row):
            if row["status"] != "placed" or pd.isna(row["log_entry"]):
                return None
            return abs(float(row["entry"]) - float(row["log_entry"])) < 0.01
        merged["entry_ok"] = merged.apply(_check_entry, axis=1)

        # Tang chi tiet fill_status cho Combo (placed != filled that su, xem
        # get_fill_outcomes()). GAN THEO VI TRI (thu tu thoi gian), KHONG
        # merge theo gia entry - phat hien bug thuc te 2026-09-01 tren
        # HK50/H2: 2 lenh khac nhau (orderId 252 va 431) trung dung
        # entryPrice=25324.6, merge theo gia lam nhan doi dong ket qua
        # (placed bao cao 249 thay vi 247 that). merged["status"]=="placed"
        # (theo dung thu tu bartime tang dan cua CSV goc) va fill_df (theo
        # dung thu tu serial trong events.json, dict giu nguyen insertion
        # order) CUNG chronological order va CUNG so luong (1 signal placed
        # = dung 1 Create Stop Order) - an toan hon nhieu so voi merge theo
        # 1 gia tri float co the trung lap.
        fill_df = get_fill_outcomes(str(run_dir))
        placed_idx = merged.index[merged["status"] == "placed"]
        if len(placed_idx) == len(fill_df):
            merged.loc[placed_idx, "fill_status"] = fill_df["fill_status"].to_numpy()
        elif not fill_df.empty:
            print(
                f"CANH BAO fidelity_lib: so dong 'placed' ({len(placed_idx)}) != so "
                f"'Create Stop Order' ({len(fill_df)}) - fill_status KHONG duoc gan tu dong, "
                f"can kiem tra thu cong (co the co sai lech thuc su, khong chi la trung gia)."
            )

    summary = {
        "strategy": strategy,
        "signal_csv": Path(signal_csv_path).name,
        "archived_run": Path(archived_run_dir).name,
        "run_window": f"{start} -> {end}" if start is not None else "unknown",
        "total_signals_in_csv": len(signals),
        **merged["status"].value_counts().to_dict(),
        "direction_mismatches": int((merged["direction_ok"] == False).sum()),  # noqa: E712
        "untraceable_volume_floor_skips": n_untraceable,
    }
    if "entry_ok" in merged.columns:
        summary["entry_mismatches"] = int((merged["entry_ok"] == False).sum())  # noqa: E712
    if "fill_status" in merged.columns:
        summary.update(merged["fill_status"].value_counts().to_dict())

    return summary, merged
