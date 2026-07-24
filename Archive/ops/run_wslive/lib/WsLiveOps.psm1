Set-StrictMode -Version Latest

function Get-WsLiveRepoRoot {
    param(
        [string]$RepoRoot = ""
    )

    if ($RepoRoot) {
        return (Resolve-Path -LiteralPath $RepoRoot).Path
    }

    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..\..")).Path
}

function Get-WsLivePaths {
    param(
        [string]$RepoRoot = ""
    )

    $root = Get-WsLiveRepoRoot -RepoRoot $RepoRoot
    $runRoot = Join-Path $root "ops\run_wslive"
    $runtimeRoot = Join-Path $runRoot "runtime"
    $runtimeLogs = Join-Path $runtimeRoot "logs"
    $dataLogs = Join-Path $root "data_provider\runtime\logs"

    [pscustomobject]@{
        RepoRoot           = $root
        RunRoot            = $runRoot
        RuntimeRoot        = $runtimeRoot
        RuntimeLogs        = $runtimeLogs
        DataLogs           = $dataLogs
        WsLiveScript       = Join-Path $root "data_provider\apps\ws_live.py"
        AppLog             = Join-Path $dataLogs "ws_live.log"
        PidFile            = Join-Path $root "data_provider\runtime\run\ws_live_runtime.pid"
        SupervisorLog      = Join-Path $runtimeLogs "ws_live_supervisor.log"
        WatchdogLog        = Join-Path $runtimeLogs "ws_live_watchdog.log"
        ConsoleLog         = Join-Path $runtimeLogs "ws_live_console.log"
        HeartbeatFile      = Join-Path $runtimeRoot "ws_live_supervisor.heartbeat.json"
        NoProcessSinceFile = Join-Path $runtimeRoot "ws_live_no_process_since.txt"
    }
}

function Initialize-WsLiveRuntime {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Paths
    )

    New-Item -ItemType Directory -Force -Path $Paths.RuntimeRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $Paths.RuntimeLogs | Out-Null
    New-Item -ItemType Directory -Force -Path $Paths.DataLogs | Out-Null
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Paths.PidFile) | Out-Null
}

function Get-WsLivePython {
    param(
        [string]$RepoRoot = "",
        [string]$PythonExe = ""
    )

    if ($PythonExe) {
        return $PythonExe
    }

    $root = Get-WsLiveRepoRoot -RepoRoot $RepoRoot
    $venvPython = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    return "python"
}

function Write-WsLiveOpsLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,
        [Parameter(Mandatory = $true)]
        [string]$LogPath,
        [ValidateSet("INFO", "WARN", "ERROR")]
        [string]$Level = "INFO",
        [switch]$Quiet
    )

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [$Level] $Message"
    Add-Content -LiteralPath $LogPath -Value $line
    if (-not $Quiet) {
        Write-Host $line
    }
}

function Get-WsLiveProcesses {
    $pattern = "data_provider[\\/]+apps[\\/]+ws_live\.py|ws_live\.py"
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -match "^pythonw?\.exe$" -and
            $_.CommandLine -match $pattern
        }
}

function Test-WsLiveProcessAlive {
    param(
        [int]$ProcessId
    )

    if ($ProcessId -le 0) {
        return $false
    }

    return [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-WsLivePidFileStatus {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Paths
    )

    $pidText = ""
    $pidValue = 0
    $exists = Test-Path -LiteralPath $Paths.PidFile
    if ($exists) {
        $pidText = (Get-Content -LiteralPath $Paths.PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($pidText -match "^\d+$") {
            $pidValue = [int]$pidText
        }
    }

    [pscustomobject]@{
        Exists = $exists
        Path   = $Paths.PidFile
        Pid    = $pidValue
        Raw    = $pidText
        Alive  = (Test-WsLiveProcessAlive -ProcessId $pidValue)
    }
}

function Invoke-WsLivePythonJson {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Paths,
        [Parameter(Mandatory = $true)]
        [string]$PythonCode,
        [string]$PythonExe = ""
    )

    $python = Get-WsLivePython -RepoRoot $Paths.RepoRoot -PythonExe $PythonExe
    Push-Location $Paths.RepoRoot
    try {
        $output = $PythonCode | & $python - 2>&1
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
    }
    finally {
        Pop-Location
    }

    if ($exitCode -ne 0) {
        return [pscustomobject]@{
            ok     = $false
            error  = "python_exit_$exitCode"
            output = ($output -join "`n")
        }
    }

    $jsonLine = $output | Select-Object -Last 1
    try {
        return ($jsonLine | ConvertFrom-Json)
    }
    catch {
        return [pscustomobject]@{
            ok     = $false
            error  = $_.Exception.Message
            output = ($output -join "`n")
        }
    }
}

function Get-WsLiveDbLockStatus {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Paths,
        [string]$PythonExe = ""
    )

    $code = @'
import json

result = {
    "ok": True,
    "exists": False,
    "task_name": None,
    "expires_at": None,
    "payload": None,
    "error": None,
}

try:
    from modules.db_connector import get_connection
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT TaskName, ExpiresAt, Payload
            FROM SEN.ActiveTask
            WHERE TaskName = ?
        """, ("ws_live_runtime",))
        row = cur.fetchone()
        if row:
            result.update({
                "exists": True,
                "task_name": str(row[0]),
                "expires_at": str(row[1]),
                "payload": None if row[2] is None else str(row[2]),
            })
    finally:
        conn.close()
except Exception as exc:
    result["ok"] = False
    result["error"] = f"{type(exc).__name__}: {exc}"

print(json.dumps(result))
'@

    Invoke-WsLivePythonJson -Paths $Paths -PythonCode $code -PythonExe $PythonExe
}

function Clear-WsLiveDbLock {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Paths,
        [string]$PythonExe = ""
    )

    $code = @'
import json

result = {"ok": True, "deleted_rows": 0, "error": None}

try:
    from modules.db_connector import get_connection
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM SEN.ActiveTask WHERE TaskName = ?", ("ws_live_runtime",))
        result["deleted_rows"] = cur.rowcount
        conn.commit()
    finally:
        conn.close()
except Exception as exc:
    result["ok"] = False
    result["error"] = f"{type(exc).__name__}: {exc}"

print(json.dumps(result))
'@

    Invoke-WsLivePythonJson -Paths $Paths -PythonCode $code -PythonExe $PythonExe
}

function Update-WsLiveSupervisorHeartbeat {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Paths,
        [Parameter(Mandatory = $true)]
        [string]$State,
        [int]$ChildExitCode = 0
    )

    Initialize-WsLiveRuntime -Paths $Paths
    $payload = [ordered]@{
        timestamp_utc  = (Get-Date).ToUniversalTime().ToString("o")
        state          = $State
        child_exit_code = $ChildExitCode
        process_id     = $PID
        host           = $env:COMPUTERNAME
    }
    $payload | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $Paths.HeartbeatFile -Encoding UTF8
}

function Get-WsLiveSupervisorHeartbeat {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Paths
    )

    if (-not (Test-Path -LiteralPath $Paths.HeartbeatFile)) {
        return $null
    }

    try {
        return (Get-Content -LiteralPath $Paths.HeartbeatFile -Raw | ConvertFrom-Json)
    }
    catch {
        return $null
    }
}

function Repair-WsLiveStaleRuntime {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Paths,
        [Parameter(Mandatory = $true)]
        [string]$LogPath,
        [string]$Reason = "unspecified",
        [int]$SettleSeconds = 5,
        [switch]$SkipDbLockRepair,
        [string]$PythonExe = ""
    )

    if ($SettleSeconds -gt 0) {
        Start-Sleep -Seconds $SettleSeconds
    }

    $live = @(Get-WsLiveProcesses)
    if ($live.Count -gt 0) {
        $ids = ($live | Select-Object -ExpandProperty ProcessId) -join ", "
        Write-WsLiveOpsLog -LogPath $LogPath -Message "stale repair skipped ($Reason): live ws_live process(es): $ids" -Quiet
        return [pscustomobject]@{
            Repaired = $false
            Reason   = "live_process"
            Pids     = $ids
        }
    }

    $pidStatus = Get-WsLivePidFileStatus -Paths $Paths
    if ($pidStatus.Exists) {
        try {
            Remove-Item -LiteralPath $Paths.PidFile -Force
            Write-WsLiveOpsLog -LogPath $LogPath -Message "removed stale pid file ($Reason): $($Paths.PidFile)" -Quiet
        }
        catch {
            Write-WsLiveOpsLog -LogPath $LogPath -Level "WARN" -Message "could not remove stale pid file ($Reason): $($_.Exception.Message)" -Quiet
        }
    }

    $dbResult = $null
    if (-not $SkipDbLockRepair) {
        $dbStatus = Get-WsLiveDbLockStatus -Paths $Paths -PythonExe $PythonExe
        if ($dbStatus.ok -and $dbStatus.exists) {
            $dbResult = Clear-WsLiveDbLock -Paths $Paths -PythonExe $PythonExe
            if ($dbResult.ok) {
                Write-WsLiveOpsLog -LogPath $LogPath -Message "cleared stale DB lock ($Reason): deleted_rows=$($dbResult.deleted_rows)" -Quiet
            }
            else {
                Write-WsLiveOpsLog -LogPath $LogPath -Level "WARN" -Message "could not clear stale DB lock ($Reason): $($dbResult.error)" -Quiet
            }
        }
        elseif (-not $dbStatus.ok) {
            Write-WsLiveOpsLog -LogPath $LogPath -Level "WARN" -Message "could not inspect DB lock ($Reason): $($dbStatus.error)" -Quiet
        }
    }

    [pscustomobject]@{
        Repaired = $true
        Reason   = "no_live_process"
        DbResult = $dbResult
    }
}

Export-ModuleMember -Function `
    Get-WsLiveRepoRoot, `
    Get-WsLivePaths, `
    Initialize-WsLiveRuntime, `
    Get-WsLivePython, `
    Write-WsLiveOpsLog, `
    Get-WsLiveProcesses, `
    Test-WsLiveProcessAlive, `
    Get-WsLivePidFileStatus, `
    Get-WsLiveDbLockStatus, `
    Clear-WsLiveDbLock, `
    Update-WsLiveSupervisorHeartbeat, `
    Get-WsLiveSupervisorHeartbeat, `
    Repair-WsLiveStaleRuntime
