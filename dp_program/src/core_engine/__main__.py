"""Package entrypoint: ``python -m core_engine ...``.

Actual argument parsing and command dispatch live in :mod:`core_engine.util.cli`.
"""

from __future__ import annotations

from core_engine.util.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
