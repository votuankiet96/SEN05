"""Central configuration loader for DP Program V3."""
# Đây là module DUY NHẤT được phép đọc Config.yaml (AGENTS.md rule 5).
# Mọi module khác chỉ nhận vào một dict `config` đã được load và validate
# sẵn, và tin tưởng tuyệt đối vào nó — nên bất cứ điều gì sai ở đây sẽ trở
# thành bất ngờ khó lường ở mọi nơi khác trong hệ thống. load_config() ở
# cuối file là điểm vào công khai duy nhất; mọi thứ phía trên nó là phần
# xử lý nội bộ được gọi tuần tự theo thứ tự:
#   1. tìm và parse file YAML
#   2. từ chối mọi key mà operator không được phép tự cấu hình (các guard
#      "data" / "tables" / "sql_server.contract_version" bên trong
#      load_config())
#   3. gộp thêm SQL contract cố định (_static_contract) và các giá trị
#      mặc định kỹ thuật bị khóa (_apply_technical_defaults)
#   4. đổi mọi đường dẫn filesystem tương đối thành đường dẫn tuyệt đối
#      (_resolve_paths)
#   5. kiểm tra kiểu dữ liệu, chuẩn hóa, và validate chéo mọi setting còn
#      lại của operator (_validate)
from __future__ import annotations
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

# Capital.com/TradingView cấp đúng token này cho một phiên chưa từng đăng
# nhập thành công thật sự; engine/auth.py::_authenticated() coi token này
# giống hệt như "không có token nào cả". Hằng số này đặt ở đây thay vì
# trong auth.py để _validate() bên dưới có thể từ chối ngay một
# Config.yaml lỡ dán nhầm guest token vào lúc load, thay vì chỉ phát hiện
# ra sau này khi engine thật sự cố kết nối.
GUEST_TOKEN = "unauthorized_user_token"


class ConfigError(ValueError):
    """Raised when Config.yaml is missing or invalid."""
    # Điểm vào CLI (__main__.py) bắt lỗi này cùng với ValueError thường,
    # rồi in ra một dòng lỗi ngắn gọn kiểu usage-error thay vì in cả stack
    # trace, để operator thấy ví dụ "sql_server requires driver, server,
    # and database" thay vì một traceback Python dài dòng.


# Timeframe live nhỏ nhất mà chương trình từng lên lịch chạy (M5 = 5
# phút). Dùng ở phần bên dưới để tính số bar tối đa mà một cửa sổ backfill
# có thể cần, để _validate() bắt được trường hợp max_bars_per_request quá
# nhỏ so với lookback_days ngay lúc load config, thay vì để lỗi xảy ra
# giữa chừng lúc backfill lúc 3 giờ sáng.
_MIN_TIMEFRAME_MINUTES = 5
# Hai đối tượng SQL mà engine luôn luôn thao tác tới. Cố tình KHÔNG đưa
# vào Config.yaml (xem guard "tables is owned by configuration.py" trong
# load_config()) để một Config.yaml gõ nhầm hoặc copy-paste từ môi trường
# khác không bao giờ có thể âm thầm chuyển hướng việc ghi dữ liệu
# production sang sai bảng hoặc sai stored procedure. Mọi module cần các
# tên này đều đọc từ config["tables"], được _static_contract() bơm vào
# bên dưới.
_TABLES = {
    "fact_table": "DWH.Fact_OHLCV",
    "load_procedure": "DWH.usp_LoadDirect",
}

# Được so sánh lúc runtime (engine/sql_connector.py::check_connection()
# và bulk_upsert_candles()) với extended property DPContractVersion được
# gắn trên DWH.usp_LoadDirect trong SQL Server. Nếu contract version của
# stored procedure đang deploy không khớp chuỗi này, chương trình sẽ
# fail-closed (dừng lại) thay vì âm thầm ghi dữ liệu theo một hình dạng
# mà procedure không còn mong đợi nữa, sau khi một trong hai bên (code
# hoặc schema SQL) đã thay đổi.
_SQL_CONTRACT_VERSION = "4"
# Các tham số kỹ thuật nội bộ mà runtime cần nhưng operator không bao giờ
# nên tự set theo từng môi trường (số lần retry, timeout, kích thước
# buffer...). Được _apply_technical_defaults() bên dưới bơm vào mọi config
# đã load, và hàm đó CŨNG từ chối thẳng config nếu Config.yaml của
# operator cố set bất kỳ key nào trong số này (lỗi ConfigError "technical
# settings are owned by configuration.py"). Cách này giữ Config.yaml chỉ
# chứa những gì operator thực sự cần thay đổi theo từng lần triển khai
# (thông tin đăng nhập, chọn symbol, lịch chạy), còn mọi thứ tinh chỉnh
# CÁCH engine vận hành bên trong thì nằm trong code, được review như mọi
# thay đổi logic khác.
_TECHNICAL_DEFAULTS = {
    # Cách engine live/backfill giao tiếp với TradingView qua WebSocket.
    "tradingview": {
        "websocket_url": "wss://prodata.tradingview.com/socket.io/websocket",
        "timezone": "Etc/UTC",
        "timeout_seconds": 45,
        "retry_count": 3,
        "retry_delay_seconds": 5,
        # Nơi lưu profile trình duyệt Playwright/Chromium dùng để lấy
        # cookie phiên đăng nhập mới (engine/auth.py).
        "browser_profile_dir": "runtime/cache/tradingview-profile",
        # Luôn đăng nhập lại từ đầu bằng trình duyệt headless thay vì cố
        # tái sử dụng một cookie cache có thể đã cũ sau mỗi lần restart.
        "headless_fresh_login": True,
        # Chủ động refresh phiên đăng nhập trước khi nó hết hạn bằng nhau
        # số giây này, và nếu lần refresh đó thất bại thì thử lại theo
        # nhịp độ này, để việc xác thực tự làm mới lặng lẽ ở nền thay vì
        # để live/backfill gặp phải một phiên đã hết hạn giữa lúc đang
        # fetch dữ liệu.
        "proactive_refresh_seconds": 900,
        "refresh_retry_seconds": 600,
    },
    # Giới hạn batch của backfill; kết hợp lúc runtime với
    # backfill.lookback_days do operator cấu hình (xem backfill.py).
    "backfill": {
        "overlap_bars": 3,
        "max_bars_per_request": 20000,
    },
    # Hành vi của service daemon chạy dài hạn (engine/runtime.py): heartbeat
    # ghi thường xuyên cỡ nào, sau khi khởi động chờ bao lâu trước khi coi
    # một lần backfill bị bỏ lỡ là vấn đề thật sự, và log file xoay vòng
    # (rotate) ra sao.
    "service": {
        "backfill_guard_seconds": 150, "startup_grace_seconds": 300,
        "heartbeat_seconds": 15,
        "log_max_bytes": 20971520,
        "log_backup_count": 30,
    },
    # Tinh chỉnh driver/kết nối SQL Server (engine/sql_connector.py). Bản
    # thân server/database/thông tin đăng nhập THÌ operator được cấu hình
    # — xem khối sql_server bên trong _validate() bên dưới — chỉ riêng
    # tên driver và hành vi retry/timeout/batch là cố định ở đây.
    "sql_server": {
        "driver": "ODBC Driver 18 for SQL Server",
        "timeout_seconds": 30,
        "command_timeout_seconds": 30,
        "retry_count": 3,
        "retry_delay_seconds": 5,
        "batch_size": 1000,
    },
}
# ---------------------------------------------------------------------
# Các hàm hỗ trợ nhỏ để kiểm tra kiểu / chuẩn hóa giá trị, dùng chung bởi
# _validate() bên dưới. Mỗi hàm raise ConfigError kèm tên field bị lỗi
# trong thông điệp, để một giá trị sai trong Config.yaml chỉ thẳng vào
# chỗ cần sửa thay vì một lỗi kiểu dữ liệu chung chung của Python. Không
# hàm nào trong số này đụng tới SQL hay filesystem — chỉ thuần validate
# giá trị.
# ---------------------------------------------------------------------
def _mapping(value: Any, name: str) -> dict[str, Any]:
    # Yêu cầu `value` phải là một YAML mapping (tức một section config).
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a mapping")
    return value
def _positive_int(value: Any, name: str) -> int:
    # Ép kiểu sang int và yêu cầu > 0; dùng cho mọi setting kiểu thời
    # gian/số lượng/kích thước (timeout, số lần retry, batch size...).
    if isinstance(value, bool):
        # bool là subclass của int trong Python, nên isinstance(True, int)
        # trả về True và int(True) == 1 — nếu không có guard này, một giá
        # trị "true"/"false" gõ nhầm vào một field kiểu số sẽ âm thầm biến
        # thành 1/0 thay vì báo lỗi config rõ ràng.
        raise ConfigError(f"{name} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a positive integer") from exc
    if result <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return result

def _boolean(value: Any, name: str) -> bool:
    # Chuẩn hóa một giá trị bool YAML, hoặc chuỗi yes/no/on/off thông
    # dụng, thành một bool Python thật sự. Cờ YAML đôi khi được gõ tay
    # dưới dạng chuỗi có ngoặc kép ("yes"/"off"...) thay vì true/false
    # trần.
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ConfigError(f"{name} must be a boolean")
def _name_list(value: Any, name: str) -> list[str]:
    # Chuẩn hóa một danh sách tên trong YAML thành chữ hoa, từ chối giá
    # trị rỗng và trùng lặp. Dùng cho live.symbols / live.timeframes: hàm
    # này chỉ kiểm tra hình dạng của lựa chọn operator đưa vào (không
    # rỗng, không có phần tử rỗng/trùng). Việc mỗi tên có thực sự tồn tại
    # — và đang enabled — trong universe symbol/timeframe lấy từ SQL hay
    # không thì được kiểm tra sau, trong
    # engine/sql_connector.py::select_pairs(), vì module này không bao
    # giờ đụng tới SQL.
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{name} must be a non-empty list")
    normalized = [str(item).strip().upper() for item in value]
    if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
        raise ConfigError(f"{name} contains an empty or duplicate value")
    return normalized

# Trả về SQL contract cố định, do code sở hữu (xem _TABLES ở trên), được
# copy ra một bản mới để caller không thể vô tình sửa vào dict cấp module.
# Được load_config() gộp vào mọi config đã load, dưới key "tables".
def _static_contract() -> dict[str, Any]:
    return {"tables": dict(_TABLES)}

# Duyệt qua _TECHNICAL_DEFAULTS từng section một. Với mỗi section, nếu
# YAML của operator đã tự định nghĩa bất kỳ key kỹ thuật nào bị khóa, raise
# lỗi ngay lập tức (fail-closed khi có ý định override); nếu không thì bơm
# các giá trị mặc định do code sở hữu vào thẳng section đó. Đây là cơ chế
# DUY NHẤT thực thi nguyên tắc "technical settings are owned by
# configuration.py" cho toàn bộ hệ thống cùng lúc, thay vì mỗi section
# phải tự viết một lượt kiểm tra override riêng.
def _apply_technical_defaults(config: dict[str, Any]) -> None:
    for section, defaults in _TECHNICAL_DEFAULTS.items():
        target = _mapping(config.get(section), section)
        overridden = sorted(set(target).intersection(defaults))
        if overridden:
            names = ", ".join(f"{section}.{key}" for key in overridden)
            raise ConfigError(f"technical settings are owned by configuration.py: {names}")
        target.update(deepcopy(defaults))

# Đổi mọi đường dẫn filesystem tương đối từ YAML thành đường dẫn tuyệt
# đối, được resolve dựa trên project root (thư mục chứa Config.yaml).
# Chương trình chạy như một service nền dài hạn, có thể được khởi động từ
# một working directory bất kỳ (ví dụ từ scheduled task hoặc một shell
# khác), nên code runtime không bao giờ được phép phải đoán "tương đối so
# với cái gì?" — mọi đường dẫn trong config trả về đều đã là tuyệt đối.
def _resolve_paths(config: dict[str, Any], root: Path) -> None:
    app = _mapping(config.get("app"), "app")
    runtime_path = Path(str(app.get("runtime_dir") or "runtime"))
    if not runtime_path.is_absolute():
        runtime_path = root / runtime_path
    app["runtime_dir"] = str(runtime_path.resolve())

    tv = _mapping(config.get("tradingview"), "tradingview")
    profile = Path(str(tv.get("browser_profile_dir") or "runtime/cache/tradingview-profile"))
    if not profile.is_absolute():
        profile = root / profile
    tv["browser_profile_dir"] = str(profile.resolve())
# Lượt chuẩn hóa + validate chính, chạy qua mọi section cấp cao nhất mà
# operator thực sự được phép cấu hình (khác với `tables` và các sub-key kỹ
# thuật, vốn bị khóa — xem _apply_technical_defaults ở trên). Sửa `config`
# trực tiếp tại chỗ (in place): mọi giá trị hàm này chạm vào đều được ép
# về đúng kiểu cuối cùng (str/bool/int) rồi gán lại, để khi hàm này chạy
# xong, code phía sau có thể tin tưởng hình dạng của mọi field mà không
# cần tự kiểm tra kiểu lại nữa.
def _validate(config: dict[str, Any]) -> None:
    # Mọi section tối thiểu phải tồn tại và là một mapping trước khi các
    # bước kiểm tra riêng theo từng section bên dưới thử đọc key ra từ nó.
    for section in (
        "app", "tradingview", "discord", "backfill", "live", "service", "sql_server", "tables"
    ):
        _mapping(config.get(section), section)

    # --- discord: báo cáo tình trạng/sự cố tùy chọn cho operator (xem
    # util/discord_report.py). Mặc định tắt; nếu bật lên thì webhook URL
    # phải thực sự trông giống một webhook Discord thật, để một URL rỗng
    # hoặc sai định dạng bị chặn ngay lúc load config thay vì mỗi lần
    # publish đều âm thầm thất bại lúc runtime.
    discord = config["discord"]
    discord["enabled"] = _boolean(discord.get("enabled", False), "discord.enabled")
    discord["webhook_url"] = str(discord.get("webhook_url") or "").strip()
    if discord["enabled"] and not discord["webhook_url"].startswith(
        ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")
    ):
        raise ConfigError("discord.webhook_url must be configured when Discord is enabled")

    # --- tradingview: các thông tin bí mật để kết nối và thông tin đăng
    # nhập dùng để xác thực phiên trình duyệt headless (engine/auth.py).
    tv = config["tradingview"]
    for key in ("auth_token", "cookie", "username", "password", "two_factor_secret"):
        tv[key] = str(tv.get(key) or "").strip()
    tv["headless_fresh_login"] = _boolean(
        tv.get("headless_fresh_login", True), "tradingview.headless_fresh_login"
    )
    if not str(tv.get("websocket_url") or "").startswith("wss://"):
        raise ConfigError("tradingview.websocket_url must start with wss://")
    tv["timeout_seconds"] = _positive_int(tv.get("timeout_seconds"), "tradingview.timeout_seconds")
    tv["retry_count"] = _positive_int(tv.get("retry_count"), "tradingview.retry_count")
    for key in ("proactive_refresh_seconds", "refresh_retry_seconds"):
        tv[key] = _positive_int(tv.get(key), f"tradingview.{key}")
    # Fail-closed thay vì âm thầm chạy như một phiên guest ẩn danh trên
    # một nguồn dữ liệu money-critical — xem comment về GUEST_TOKEN ở đầu
    # file này để hiểu tại sao đúng chuỗi này lại nguy hiểm.
    if tv.get("auth_token") == GUEST_TOKEN:
        raise ConfigError("guest TradingView token is not permitted")

    # --- live: workflow chạy liên tục kiểu "bám theo các candle mới
    # nhất" (engine/live.py), khác với workflow backfill lịch sử bên
    # dưới. Ở đây chỉ kiểm tra hình dạng *lựa chọn* symbol/timeframe của
    # operator (_name_list); việc mỗi tên có thực sự tồn tại — và đang
    # enabled — trong universe lấy từ SQL hay không thì được kiểm tra sau
    # bởi engine/sql_connector.py::select_pairs().
    live = config["live"]
    live["enabled"] = _boolean(live.get("enabled", True), "live.enabled")
    live["closed_candles_only"] = _boolean(
        live.get("closed_candles_only", True), "live.closed_candles_only"
    )
    live["interval_minutes"] = _positive_int(live.get("interval_minutes"), "live.interval_minutes")
    live["bars_per_request"] = _positive_int(live.get("bars_per_request"), "live.bars_per_request")
    live["symbols"] = _name_list(live.get("symbols"), "live.symbols")
    live["timeframes"] = _name_list(live.get("timeframes"), "live.timeframes")
    # Giao một candle chưa đóng (còn đang hình thành) sẽ ghi vào warehouse
    # một giá trị chắc chắn sẽ đổi ở tick tiếp theo — cờ này tồn tại chỉ để
    # có thể kiểm tra/log lại, không bao giờ được thực sự tắt trong
    # production. Xem bộ lọc `closed_only` trong
    # engine/pipeline.py::validate_candles(), đó mới là nơi setting này
    # thực sự được áp dụng lên dữ liệu từ provider.
    if not live["closed_candles_only"]:
        raise ConfigError("live.closed_candles_only must remain true for production delivery")

    # --- backfill: workflow theo lịch, xử lý cửa sổ dữ liệu lịch sử
    # (engine/backfill.py), lấp đầy/sửa lại các candle cũ mà workflow live
    # không đụng tới.
    backfill = config["backfill"]
    backfill["enabled"] = _boolean(backfill.get("enabled", True), "backfill.enabled")
    backfill["run_on_start"] = _boolean(
        backfill.get("run_on_start", True), "backfill.run_on_start"
    )
    # Bẫy cố ý: một Config.yaml cũ còn dùng key scan_bars đã bị loại bỏ sẽ
    # bị âm thầm bỏ qua (không bao giờ được áp dụng) thay vì báo lỗi, và
    # operator sẽ không nhận ra cửa sổ backfill của mình không như họ
    # tưởng.
    if "scan_bars" in backfill:
        raise ConfigError("backfill.scan_bars was replaced by backfill.lookback_days")
    for key in ("lookback_days", "overlap_bars", "max_bars_per_request"):
        backfill[key] = _positive_int(backfill.get(key), f"backfill.{key}")
    # Trường hợp xấu nhất là timeframe nhỏ nhất được cấu hình (candle 5
    # phút) cần đủ số bar để phủ toàn bộ cửa sổ lookback trong một request,
    # cộng thêm một khoảng overlap nhỏ. Kiểm tra ngay lúc load config xem
    # max_bars_per_request có thực sự đáp ứng được không, thay vì phát
    # hiện ra thiếu hụt giữa chừng lúc đang backfill.
    required_bars = (
        backfill["lookback_days"] * 24 * 60 + _MIN_TIMEFRAME_MINUTES - 1
    ) // _MIN_TIMEFRAME_MINUTES + backfill["overlap_bars"]
    if required_bars > backfill["max_bars_per_request"]:
        raise ConfigError(
            "backfill.max_bars_per_request cannot cover lookback_days "
            "for the smallest timeframe"
        )
    # schedule_utc: danh sách các mốc giờ "HH:MM" theo UTC trong ngày mà
    # lượt backfill hàng ngày sẽ chạy (bộ lập lịch trong engine/runtime.py
    # đọc giá trị này).
    slots = backfill.get("schedule_utc")
    if not isinstance(slots, list) or not slots:
        raise ConfigError("backfill.schedule_utc must be a non-empty list")
    for slot in slots:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(slot)):
            raise ConfigError(f"invalid UTC backfill schedule: {slot}")

    # --- service: các tham số tinh chỉnh daemon nội bộ mà operator có
    # thể điều chỉnh theo từng lần triển khai (khác với các giá trị mặc
    # định kỹ thuật cố định ở phần trên file) — nhịp heartbeat, thời gian
    # ân hạn (grace period) lúc khởi động, và việc xoay vòng log.
    service = config["service"]
    for key in ("backfill_guard_seconds", "startup_grace_seconds", "heartbeat_seconds", "log_max_bytes", "log_backup_count"):
        service[key] = _positive_int(service.get(key), f"service.{key}")

    # --- sql_server: cách kết nối tới SQL Server warehouse. Hoặc dùng
    # trusted_connection kiểu Windows, hoặc dùng cặp username+password
    # tường minh — không bao giờ được trộn cả hai (kiểm tra ở cuối khối
    # này).
    sql = config["sql_server"]
    for key in ("trusted_connection", "trust_server_certificate"):
        sql[key] = _boolean(sql.get(key), f"sql_server.{key}")
    for key in ("server", "database", "port", "username", "password"):
        sql[key] = str(sql.get(key) or "").strip()
    for key in ("timeout_seconds", "command_timeout_seconds", "retry_count", "batch_size"):
        sql[key] = _positive_int(sql.get(key), f"sql_server.{key}")
    if not sql.get("server") or not sql.get("database") or not sql.get("driver"):
        raise ConfigError("sql_server requires driver, server, and database")
    if bool(sql.get("username")) != bool(sql.get("password")):
        raise ConfigError("SQL username and password must be configured together")


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the single private Config.yaml and validate the result."""
    # Mặc định tìm Config.yaml nằm cạnh package đã cài đặt (đi lên hai cấp
    # thư mục từ file này: src/dp_program/configuration.py -> project
    # root), nếu không có thì fallback về thư mục làm việc hiện tại. Cách
    # này giúp cùng một đoạn code tìm được config của nó dù được chạy từ
    # gốc repo (`python -m dp_program ...`) hay được gọi theo cách khác mà
    # working directory không chắc chắn là project root.
    project_config = Path(__file__).resolve().parents[2] / "Config.yaml"
    default_config = project_config if project_config.is_file() else Path.cwd() / "Config.yaml"
    config_path = Path(path) if path else default_config
    config_path = config_path.resolve()
    if not config_path.is_file():
        raise ConfigError(f"configuration file not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read configuration: {exc}") from exc
    config = deepcopy(_mapping(raw, "Config.yaml"))
    # Ba guard này từ chối bất kỳ Config.yaml nào cố nhét vào những giá trị
    # mà chương trình này không coi là operator-configurable:
    #   - "data": universe symbol/timeframe nằm trong SQL
    #     (DWH.Dim_Symbol / DWH.Dim_Timeframe), được
    #     engine/sql_connector.py::fetch_universe() đọc trực tiếp —
    #     Config.yaml không có quyền quyết định symbol/timeframe nào tồn
    #     tại, chỉ được chọn workflow live sẽ dùng những cái nào trong số
    #     đó (live.symbols / live.timeframes ở trên).
    #   - "tables": cố định bởi _TABLES ở trên, không thay đổi theo từng
    #     lần triển khai.
    #   - "sql_server.contract_version": cố định bởi _SQL_CONTRACT_VERSION
    #     ở trên, gắn với phiên bản code đang deploy, không phải theo môi
    #     trường.
    if "data" in config:
        raise ConfigError("data is owned by SQL dimensions and cannot be overridden")
    if "tables" in config:
        raise ConfigError("tables is owned by configuration.py and cannot be overridden")
    sql = _mapping(config.get("sql_server"), "sql_server")
    if "contract_version" in sql:
        raise ConfigError(
            "sql_server.contract_version is owned by configuration.py and cannot be overridden"
        )
    config.update(_static_contract())
    sql["contract_version"] = _SQL_CONTRACT_VERSION
    _apply_technical_defaults(config)
    _resolve_paths(config, config_path.parent)
    _validate(config)
    # Lưu lại để các lệnh chẩn đoán (ví dụ `dp_program settings`/`doctor`)
    # có thể hiển thị cho operator biết chính xác file nào trên đĩa đã tạo
    # ra config này.
    config["app"]["config_path"] = str(config_path)
    return config
