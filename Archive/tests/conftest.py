from __future__ import annotations

from pathlib import Path


def pytest_ignore_collect(collection_path, config) -> bool:  # type: ignore[no-untyped-def]
    """Ignore runtime pytest cache/temp trees."""
    _ = config
    path = Path(str(collection_path))
    return "tests" in path.parts and "pytest" in path.parts
