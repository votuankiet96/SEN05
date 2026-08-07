"""Application-wide structured logging and secret-safe error text."""
# File này định dạng log cho toàn bộ chương trình.
# Hai việc chính: che secret và ghi mỗi event thành một dòng key=value.
# Runtime live/backfill dùng log riêng, nhưng cùng format để dễ truy vấn.

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


# Tên event log dùng SCREAMING_SNAKE_CASE, ví dụ LIVE_CYCLE_COMPLETED.
_EVENT = re.compile(r"^[A-Z][A-Z0-9_]*$")
# Tên component/field dùng snake_case chữ thường.
_FIELD = re.compile(r"^[a-z][a-z0-9_]*$")
# Giá trị khớp regex này được in thẳng; còn lại sẽ bọc JSON string.
_PLAIN = re.compile(r"^[A-Za-z0-9_.:/@+-]+$")
# Các mức rủi ro thống nhất cho log và Discord reporter.
_RISKS = {"NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
# Field có tên giống secret thì che toàn bộ giá trị.
_SECRET_KEY = re.compile(
    r"(?:^|_)(?:api_key|auth_token|authorization|connection_string|"
    r"cookie|credential|password|pwd|secret|token|uid|username)(?:$|_)"
)
# Che các cụm dạng password=... hoặc cookie: ... bên trong text lỗi.
_SECRET_VALUE = re.compile(
    r"(?i)\b(password|pwd|cookie|authorization|auth[_ -]?token|"
    r"api[_ -]?key|secret|uid|user(?:name|[ _-]?id))\s*[:=]\s*"
    r"(?:bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^,;\s]+)"
)
# Che chuỗi trông giống JWT nếu nó lọt vào exception/message.
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*\b")


def _redact_text(value: str) -> str:
    # Log phải nằm trên một dòng, nên đổi xuống dòng thành khoảng trắng.
    # Sau đó che secret dạng key=value và JWT.
    text = value.replace("\r", " ").replace("\n", " ")
    text = _SECRET_VALUE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return _JWT.sub("[REDACTED]", text)


def safe_error(error: BaseException, *, limit: int = 300) -> str:
    """Return one bounded, single-line exception description without credentials."""
    # Cắt message lỗi quá dài để log không phình và vẫn đọc được timeline.
    text = _redact_text(f"{type(error).__name__}: {error}")
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _format_value(key: str, value: Any) -> str:
    # Nếu tên field là secret thì che ngay, không cần xét nội dung.
    if _SECRET_KEY.search(key):
        value = "[REDACTED]"
    elif isinstance(value, BaseException):
        value = safe_error(value)
    elif isinstance(value, datetime):
        value = value.isoformat()
    # None/bool in giống JSON để dễ parse bằng công cụ ngoài.
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    # Text có khoảng trắng/ký tự đặc biệt được bọc JSON string để không vỡ key=value.
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
    # Kiểm format event/risk/component/field trước khi ghi log.
    # Nếu caller truyền sai, báo lỗi ngay để không sinh log khó parse.
    if not _EVENT.fullmatch(event):
        raise ValueError(f"invalid log event: {event}")
    if risk not in _RISKS:
        raise ValueError(f"invalid log risk: {risk}")
    if not _FIELD.fullmatch(component):
        raise ValueError(f"invalid log component: {component}")
    invalid = [key for key in fields if not _FIELD.fullmatch(key)]
    if invalid:
        raise ValueError(f"invalid log fields: {', '.join(invalid)}")
    # Chấp nhận level dạng chuỗi hoặc số logging gốc của Python.
    number = (
        getattr(logging, level.upper(), logging.INFO)
        if isinstance(level, str)
        else int(level)
    )
    # Gắn pid vào mọi dòng để phân biệt live/backfill/CLI khi chạy song song.
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
    # Nếu gọi lại trong cùng process thì không thêm handler trùng.
    if getattr(root, "_dp_program_configured", False):
        return
    # Live và backfill ghi hai file riêng để rotate log không giẫm lên nhau.
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
    # Timestamp log luôn là UTC, ký hiệu bằng Z ở cuối.
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    # File log xoay vòng theo kích thước và số bản lưu trong config["service"].
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=int(config["service"]["log_max_bytes"]),
        backupCount=int(config["service"]["log_backup_count"]),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    # Vừa ghi file, vừa in console để operator thấy realtime khi chạy foreground.
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console)
    # Đánh dấu đã cấu hình xong để lần gọi sau không cấu hình lặp.
    root._dp_program_configured = True
