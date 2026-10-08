"""Cấu hình pytest: thêm `src` vào sys.path để chạy `python -m pytest test` mà không cần PYTHONPATH."""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
sys.dont_write_bytecode = True


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "integration: chạy trên Redis thật (db trong config.yaml); bật bằng DPS_INTEGRATION=1")
