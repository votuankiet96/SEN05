"""Nạp og_config.yaml — nguồn duy nhất cho MỌI module đọc config.

Trước 2026-09-22 có 2 loader độc lập cùng parse file này (candle_reader.py
và configuration.py), mỗi bên một bản `_require()` riêng. Gộp về đây để 1
giá trị chỉ tồn tại ở đúng 1 nơi (CLAUDE.md mục 2).

File này là tầng đáy: KHÔNG import gì từ order_gateway.src khác, nên không
thể tạo vòng phụ thuộc.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / "og_config.yaml"


def load_raw(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Parse og_config.yaml. Thiếu file = raise ngay, không trả dict rỗng:
    tiến trình live không có config thì không có gì để chạy đúng cả.
    """
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def require(cfg: dict[str, Any], key: str, path: str) -> Any:
    """Đọc bắt buộc 1 key — không có giá trị mặc định nào trong code.
    Thiếu key sẽ raise ngay, chỉ rõ đúng đường dẫn cần điền.
    """
    if key not in cfg:
        raise KeyError(f"og_config.yaml thiếu '{path}'")
    return cfg[key]


# Parse đúng 1 lần cho cả tiến trình.
CONFIG: dict[str, Any] = load_raw()
