# Tick Program Analysis — superseded

This file described the pre-refactor architecture (`historical_pulling.py`,
`scheduler.py`, `backend_engine.py`, the `tick_engine/data_storage` /
`reporting` / `utils_support` / `tick_datacheck` subpackages, and the
`.env`-based config). That code has been replaced by a 10-file engine with
YAML config and no dashboard.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the current
architecture. See git history for the original content of this file if the
pre-refactor design is needed for reference.
