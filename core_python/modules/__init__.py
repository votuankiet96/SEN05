"""Bridge package for legacy ``modules.*`` imports inside ``core_python``.

The real shared modules live at the repository root in ``/modules``.
Because ``core_python`` is usually inserted early in ``sys.path``, the tiny
package in ``core_python/modules`` would otherwise shadow the real package and
break imports such as ``modules.data_loader``.
"""

from __future__ import annotations

from pathlib import Path
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

_ROOT_MODULES = Path(__file__).resolve().parents[2] / "modules"
if _ROOT_MODULES.exists():
    root_modules_str = str(_ROOT_MODULES)
    if root_modules_str not in __path__:
        __path__.append(root_modules_str)
