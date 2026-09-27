[CmdletBinding()]
param(
    [string]$OutputDir = "C:\Users\Administrator\Desktop\tick_program\run_tick"
)

# Builds tick_program.exe (PyInstaller, --onedir) from this repo's src/ and
# stages the whole output folder into $OutputDir (a run_tick/ deployment
# folder -- see $OutputDir\DEPLOY.md). Does NOT touch any Scheduled Task or
# the live service; this only produces/refreshes the .exe + _internal/.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\build_exe.ps1
#
# --onedir, not --onefile: confirmed live (2026-09-02) that a --onefile
# build launched via `\SEN05\SEN05 Tick Program Engine` (Task Scheduler,
# S4U logon, no interactive session) hangs indefinitely with ZERO log
# output -- process alive and "Responding" per Get-Process, but nothing
# ever gets written, reproduced twice in an isolated test (nothing else
# running) with 60s+ waits. The exact same exe launched manually (from an
# interactive shell) works correctly every time and did so for 4.5 days
# straight. This points at PyInstaller onefile's self-extract-then-relaunch
# bootloader dance hitting a snag specific to the non-interactive/S4U
# session (a known class of issue for onefile builds under Windows
# services/scheduled tasks) rather than anything in this app's own code.
# --onedir sidesteps it entirely: no temp-extraction step, the interpreter
# and all dependencies are already on disk as loose files next to the exe.
# The deployment shape changes because of this: $OutputDir now gets a
# tick_program.exe *and* an _internal\ folder next to it -- both must be
# copied together, DEPLOY.md documents this.
#
# --collect-all on ctrader_open_api/twisted: both do reactor/protobuf-module
# selection that plain static import analysis can miss (Twisted lazily
# installs a platform-default reactor implementation the first time
# twisted.internet.reactor is imported; protobuf-generated packages
# sometimes carry data files PyInstaller's default scan skips) -- safer to
# bundle everything in these two than hand-list every hidden import.
# --collect-all on tzdata: Windows has no OS-level IANA timezone database,
# so zoneinfo.ZoneInfo(...) (used by the real cTrader trading-schedule
# check, notify.py::is_market_closed) resolves from the `tzdata` pip
# package's bundled data files at runtime -- these are DATA, not Python
# modules, so a plain import scan silently misses them and the schedule
# check would quietly fall back to the less-accurate SESSION_RULES table
# on every symbol, on a machine with no other Python/tzdata around to
# notice against.
# --add-data on lightweight-charts.js: src/chart/server.py is a plain .py
# module (PyInstaller's import scan finds and bundles it correctly, into
# the PYZ archive), but the .js file living next to it is a loose data
# file with no import statement pointing at it -- confirmed missing from
# a build without this flag (checked with pyi_archive_viewer, not
# assumed), which would make `chart`/menu option 6 fail at runtime with
# "Offline chart asset is missing". Destination path must match
# src/chart/ so Path(__file__).with_name(...) in server.py resolves it.

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $RepoRoot

python -m PyInstaller `
    --name tick_program `
    --onedir `
    --console `
    --clean `
    --noconfirm `
    --distpath "$RepoRoot\dist" `
    --workpath "$RepoRoot\build" `
    --specpath "$RepoRoot\build" `
    --collect-all ctrader_open_api `
    --collect-all twisted `
    --collect-all tzdata `
    --add-data "$RepoRoot\src\chart\lightweight-charts.js;src\chart" `
    "$RepoRoot\scripts\windows\tick_program_entry.py"

if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit code $LASTEXITCODE)." }

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
Copy-Item -Force -Recurse "$RepoRoot\dist\tick_program\*" "$OutputDir\"
Write-Host "[ok] Built and copied tick_program.exe + _internal\ to $OutputDir"
