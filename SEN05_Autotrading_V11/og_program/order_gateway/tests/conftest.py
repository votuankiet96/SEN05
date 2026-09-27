"""Cho phép `import order_gateway.src.*` khi chạy pytest từ bất kỳ thư mục nào.

order_gateway là namespace package (không có __init__.py, xem CLAUDE.md mục
2), nên pytest không tự đặt gốc repo vào sys.path. Chèn thẳng ở đây thay vì
bắt người chạy phải nhớ set PYTHONPATH.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
