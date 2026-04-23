from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_PROVIDER_ROOT = REPO_ROOT / "data_provider"

for path in (REPO_ROOT, DATA_PROVIDER_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


try:
    import pyodbc  # noqa: F401
except ImportError:
    pyodbc_stub = types.SimpleNamespace(
        Error=Exception,
        IntegrityError=Exception,
        Connection=object,
        connect=lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("pyodbc stub connect() should not be used in unit tests")
        ),
    )
    sys.modules["pyodbc"] = pyodbc_stub


@pytest.fixture
def load_module():
    loaded_names: list[str] = []

    def _load(relative_path: str, alias: str):
        module_name = f"tests_{alias}_{uuid.uuid4().hex}"
        module_path = REPO_ROOT / relative_path
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        loaded_names.append(module_name)
        spec.loader.exec_module(module)
        return module

    yield _load

    for module_name in loaded_names:
        sys.modules.pop(module_name, None)
