"""Application-wide structured logging and secret-safe error text."""
# Đây là module logging dùng chung cho toàn bộ engine — hầu như mọi module
# khác (pipeline.py, sql_connector.py, live.py, backfill.py qua runtime.py,
# auth.py, websocket.py, spool.py, util/discord_report.py) đều import
# log_event() từ đây để ghi log, và __main__.py gọi configure_logging()
# đúng một lần lúc khởi động tiến trình run-live/run-backfill. File này có
# hai việc tách biệt:
#   - safe_error()/_redact_text()/_format_value(): đảm bảo không có bí mật
#     nào (mật khẩu, token, cookie...) bị lọt vào log, dù cố tình hay vô
#     tình truyền vào.
#   - log_event()/configure_logging(): định dạng mọi dòng log theo đúng
#     một chuẩn key=value thống nhất, và thiết lập handler ghi ra file
#     xoay vòng (rotate) theo từng vai trò tiến trình (live/backfill).

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


# Tên event log phải là SCREAMING_SNAKE_CASE, ví dụ "LIVE_CYCLE_COMPLETED",
# "PAIR_FAILED" — dùng để validate tham số `event` trong log_event().
_EVENT = re.compile(r"^[A-Z][A-Z0-9_]*$")
# Tên field/component phải là snake_case chữ thường — dùng để validate cả
# `component` lẫn tên của mọi field bổ sung (**fields) trong log_event().
_FIELD = re.compile(r"^[a-z][a-z0-9_]*$")
# Tập ký tự được coi là "an toàn", có thể in thẳng ra mà không cần bọc
# ngoặc kép/escape trong dòng log key=value. Giá trị nào không khớp hết
# tập ký tự này (ví dụ chứa khoảng trắng, dấu ngoặc kép, ký tự unicode...)
# sẽ bị _format_value() chuyển sang dạng JSON string để không phá vỡ định
# dạng key=value một dòng.
_PLAIN = re.compile(r"^[A-Za-z0-9_.:/@+-]+$")
# Tập risk level cố định dùng xuyên suốt hệ thống — log_event() từ chối
# bất kỳ giá trị risk nào ngoài tập này. util/discord_report.py dựa vào
# đúng các mức risk này để quyết định màu embed và có nên throttle/publish
# cảnh báo hay không.
_RISKS = {"NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
# Khớp TÊN field mà bản chất luôn luôn là bí mật (api_key, auth_token,
# password, cookie...). Nếu key của một field khớp regex này, giá trị của
# field đó bị redact hoàn toàn trong _format_value(), bất kể nội dung thật
# sự là gì.
_SECRET_KEY = re.compile(
    r"(?:^|_)(?:api_key|auth_token|authorization|connection_string|"
    r"cookie|credential|password|pwd|secret|token|uid|username)(?:$|_)"
)
# Khớp các cụm kiểu "password=hunter2" hay "cookie: abc123" xuất hiện BÊN
# TRONG một chuỗi văn bản bất kỳ (ví dụ lọt vào message của một exception),
# kể cả khi field chứa chuỗi đó không có tên khớp _SECRET_KEY ở trên. Đây
# là lớp phòng thủ thứ hai, bắt các bí mật "trốn" bên trong nội dung text
# tự do thay vì được truyền như một field riêng có tên rõ ràng.
_SECRET_VALUE = re.compile(
    r"(?i)\b(password|pwd|cookie|authorization|auth[_ -]?token|"
    r"api[_ -]?key|secret|uid|user(?:name|[ _-]?id))\s*[:=]\s*"
    r"(?:bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^,;\s]+)"
)
# Khớp một chuỗi trông giống JWT (token TradingView chính là JWT, xem
# engine/auth.py::_claims()) lọt vào bên trong một chuỗi văn bản tự do —
# lớp phòng thủ bổ sung tương tự _SECRET_VALUE nhưng riêng cho định dạng
# JWT ba đoạn base64url cách nhau bởi dấu chấm.
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*\b")


def _redact_text(value: str) -> str:
    # Gộp mọi xuống dòng thành khoảng trắng trước — mỗi sự kiện log phải
    # nằm gọn trên đúng một dòng (log_event() nối mọi field bằng dấu cách
    # rồi ghi một lần bằng logger.log()), nên một message exception nhiều
    # dòng không được phép làm vỡ định dạng đó. Sau đó lần lượt xóa các
    # mẫu bí mật dạng key=value rồi tới các chuỗi giống JWT.
    text = value.replace("\r", " ").replace("\n", " ")
    text = _SECRET_VALUE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return _JWT.sub("[REDACTED]", text)


def safe_error(error: BaseException, *, limit: int = 300) -> str:
    """Return one bounded, single-line exception description without credentials."""
    # Giới hạn độ dài (mặc định 300 ký tự) để một exception có message dài
    # bất thường (ví dụ traceback lồng trong message) không làm phình to
    # một dòng log; cắt bớt và thêm "..." nếu vượt giới hạn.
    text = _redact_text(f"{type(error).__name__}: {error}")
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _format_value(key: str, value: Any) -> str:
    # Nếu TÊN field đã khớp _SECRET_KEY thì redact toàn bộ giá trị, bất
    # kể type hay nội dung là gì — an toàn theo tên field, không phụ
    # thuộc caller có nhớ redact hay không.
    if _SECRET_KEY.search(key):
        value = "[REDACTED]"
    elif isinstance(value, BaseException):
        value = safe_error(value)
    elif isinstance(value, datetime):
        value = value.isoformat()
    # None/bool in ra kiểu chữ thường giống JSON ("null"/"true"/"false")
    # thay vì kiểu Python ("None"/"True"/"False"), để dòng log nhất quán
    # và dễ parse bằng công cụ ngoài hơn là chỉ đọc bằng mắt.
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    # Với mọi giá trị còn lại (chuỗi...): redact nếu có mẫu bí mật lọt
    # vào nội dung, sau đó in thẳng nếu khớp tập ký tự "an toàn" _PLAIN,
    # hoặc bọc thành JSON string (có escape) nếu không — để một giá trị
    # chứa khoảng trắng/ký tự đặc biệt không thể bị hiểu nhầm là ranh
    # giới sang field key=value kế tiếp.
    text = _redact_text(str(value))
    return text if _PLAIN.fullmatch(text) else json.dumps(text, ensure_ascii=True)


def log_event(
    logger: logging.Logger,
    level: int | str,
    event: str,
    risk: str,
    *,
    component: str,
    **fields: Any,
) -> None:
    """Write one stable key=value event suitable for humans and simple queries."""
    event = str(event).upper()
    risk = str(risk).upper()
    component = str(component).lower()
    # Validate nghiêm ngặt hình dạng của event/risk/component/tên field:
    # nếu nơi gọi log_event() truyền sai định dạng, raise lỗi ngay lập tức
    # thay vì âm thầm ghi ra một dòng log méo mó, khó parse — lỗi ở chỗ
    # gọi log phải lộ ra ngay lúc code chạy/test, không phải lúc đang mò
    # log để điều tra sự cố khác.
    if not _EVENT.fullmatch(event):
        raise ValueError(f"invalid log event: {event}")
    if risk not in _RISKS:
        raise ValueError(f"invalid log risk: {risk}")
    if not _FIELD.fullmatch(component):
        raise ValueError(f"invalid log component: {component}")
    invalid = [key for key in fields if not _FIELD.fullmatch(key)]
    if invalid:
        raise ValueError(f"invalid log fields: {', '.join(invalid)}")
    # Cho phép `level` là tên chuỗi ("INFO"/"ERROR"...) hoặc số logging
    # gốc của Python — quy về đúng một số nguyên trước khi gọi logger.log().
    number = (
        getattr(logging, level.upper(), logging.INFO)
        if isinstance(level, str)
        else int(level)
    )
    # "pid" luôn được tự động gắn vào mọi dòng log: hệ thống chạy đồng
    # thời nhiều tiến trình Python độc lập (run-live, run-backfill, các
    # lệnh CLI một lần...), nên mỗi dòng log phải tự nêu rõ được ghi bởi
    # tiến trình OS nào, kể cả khi nhiều tiến trình cùng ghi những event
    # trùng tên.
    values = {
        "component": component,
        "event": event,
        "risk": risk,
        "pid": os.getpid(),
        **fields,
    }
    logger.log(number, " ".join(f"{key}={_format_value(key, value)}" for key, value in values.items()))


def configure_logging(config: dict[str, Any], *, role: str = "live") -> None:
    """Configure one role-specific bounded log without cross-process rollover."""
    root = logging.getLogger()
    # Idempotent theo tiến trình: nếu hàm này lỡ được gọi nhiều lần trong
    # cùng một tiến trình (ví dụ do lỗi ở chỗ gọi), lần gọi sau sẽ không
    # thêm handler trùng lặp — tránh mỗi dòng log bị in lặp nhiều lần.
    if getattr(root, "_dp_program_configured", False):
        return
    # Mỗi vai trò tiến trình ghi vào một file log RIÊNG. run-live và
    # run-backfill là hai tiến trình OS độc lập chạy song song 24/7; nếu
    # dùng chung một file, việc xoay vòng (rotate) log của RotatingFileHandler
    # ở hai tiến trình khác nhau có thể giẫm lên nhau và làm hỏng file.
    filenames = {
        "live": "dp_program_live.log",
        "backfill": "dp_program_backfill.log",
    }
    if role not in filenames:
        raise ValueError(f"invalid logging role: {role}")
    level = getattr(
        logging,
        str(config["app"].get("log_level", "INFO")).upper(),
        logging.INFO,
    )
    log_path = Path(config["app"]["runtime_dir"]) / "logs" / filenames[role]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # "Z" gắn cứng vào cuối định dạng thời gian, kết hợp với
    # formatter.converter = time.gmtime bên dưới, để mọi timestamp trong
    # log luôn là UTC rõ ràng — không để operator phải đoán giờ local hay
    # UTC khi đối chiếu log giữa nhiều tiến trình/máy.
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    # File log xoay vòng theo kích thước/số bản lưu lấy từ
    # config["service"] (service.log_max_bytes/log_backup_count — xem các
    # giá trị mặc định kỹ thuật tương ứng trong configuration.py).
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=int(config["service"]["log_max_bytes"]),
        backupCount=int(config["service"]["log_backup_count"]),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    # Ghi song song ra console: khi chạy foreground (ví dụ qua
    # run_live.bat/run_backfill.bat) operator vẫn nhìn thấy log ngay trên
    # màn hình, đồng thời log vẫn được lưu bền vào file xoay vòng.
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console)
    # Đánh dấu tiến trình này đã cấu hình xong logging — cờ này chính là
    # điều kiện được kiểm tra ở đầu hàm để đảm bảo tính idempotent.
    root._dp_program_configured = True
