"""Shared process exit codes for the DP Program CLI and its subprocesses.

These are process exit codes (what `main()` returns / `sys.exit()` raises),
not HTTP or application-level status codes. `run`, `live`, and `historical`
each exit with `EXIT_LOCK_CONFLICT` when they cannot acquire their runtime
lock, and with `EXIT_CANCELLED` after a cooperative stop request, so the
supervisor and operator tooling can interpret an exit code the same way
regardless of which subcommand produced it.
"""

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_LOCK_CONFLICT = 5
EXIT_CANCELLED = 130  # Unix convention: 128 + SIGINT(2), used for a cooperative stop.
