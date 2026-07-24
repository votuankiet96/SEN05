# Claude Code — SEN05 Autotrading

> **Auto-loaded by Claude Code at session start.**
> Read `AI_BRIEFING.md` first for full system context.

---

## Quick Start

1. Read `AI_BRIEFING.md` — architecture, locked contracts, danger zones, task routing
2. Read `docs/ARCHITECTURE.md` — detailed system flow
3. Read the relevant `docs/ai_state/*.md` for the module you are working on

---

## Behavior Rules

- **Never place orders** — Python side is signal-only. No broker API calls for execution.
- **Never modify `SEN.ActiveTask` outside designated functions** — runtime_lock.py and TickDataOps.psm1 are the only allowed paths.
- **Timestamps = UTC naive** — no tz-aware datetimes, no DST conversion, no `.replace(tzinfo=...)`.
- **Signal direction = integer** — 1 for BUY, -1 for SELL. Never string.
- **PowerShell scripts are VM-DP only** — never suggest running `*.ps1` from `ops/run_tickdata/` on VM-OG (Ubuntu).
- **Do not run `install_tick_tasks.ps1` without explicit user confirmation** — it modifies live Task Scheduler entries.

---

## After Completing a Task

Update `docs/ai_state/{relevant_module}.md` with:
- What changed (file, function, behavior)
- Current known limitations
- Date (UTC)

---

## Memory

Persistent memory for this project is at:
`C:\Users\ADMIN\.claude\projects\Z--SEN05-Autotrading\memory\`

Check `MEMORY.md` index at session start for project notes not derivable from code.
