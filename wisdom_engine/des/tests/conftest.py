"""Cho phép `import wisdom_engine.des.src.*` khi chạy pytest từ bất kỳ thư mục nào.

wisdom_engine là namespace package (không có __init__.py, xem CLAUDE.md mục
2), nên pytest không tự đặt gốc repo vào sys.path. Chèn thẳng ở đây thay vì
bắt người chạy phải nhớ set PYTHONPATH.
"""

from __future__ import annotations

import sys
from pathlib import Path

# des/tests/conftest.py -> parents[3] là thư mục CHA của repo (nơi `wisdom_engine` là package gốc).
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
