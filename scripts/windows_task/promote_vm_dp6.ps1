[CmdletBinding()]
param(
    [string]$SourceRoot = 'C:\Share\dp_program',
    [string]$TaskPath = '\SEN05\',
    [string]$TaskName = 'SEN05 DP Program 24x7',
    [string]$SqlServer = 'localhost',
    [string]$Database = 'SEN05_AutoTrading',
    [string]$CandidateManifest = '',
    [string]$ServicePython = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'promote_vm_dp6.ps1 must run from an elevated PowerShell session on VM-DP6.'
    }
}

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory=$true)][string]$Executable,
        [Parameter(Mandatory=$true)][string[]]$Arguments,
        [Parameter(Mandatory=$true)][string]$WorkingDirectory,
        [Parameter(Mandatory=$true)][string]$EvidenceFile
    )
    Push-Location $WorkingDirectory
    try {
        & $Executable @Arguments 2>&1 | Tee-Object -FilePath $EvidenceFile
        $code = $LASTEXITCODE
        if ($code -ne 0) {
            throw "$Executable exited with code $code. Evidence: $EvidenceFile"
        }
    }
    finally { Pop-Location }
}

function Invoke-SqlFileChecked([string]$Path, [string]$EvidenceFile, [string]$Commit) {
    Invoke-NativeChecked -Executable 'sqlcmd' `
        -Arguments @('-b', '-r1', '-S', $SqlServer, '-E', '-d', $Database,
                     '-v', "DeploymentCommit=$Commit", '-i', $Path) `
        -WorkingDirectory $SourceRoot -EvidenceFile $EvidenceFile
}

function Wait-TaskNotRunning([int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        $state = (Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName).State
        if ($state -ne 'Running') { return $true }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Remove-JunctionOnly([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
        throw "Refusing to remove non-junction rollback path: $Path"
    }
    Remove-Item -LiteralPath $Path -Force
}

Assert-Administrator
$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
if (-not $CandidateManifest) {
    $CandidateManifest = Join-Path $SourceRoot 'runtime\deploy\artifacts\production_candidate.json'
}
$CandidateManifest = (Resolve-Path -LiteralPath $CandidateManifest).Path
$candidate = Get-Content -LiteralPath $CandidateManifest -Raw | ConvertFrom-Json
$commit = ([string]$candidate.release_commit).Trim().ToLowerInvariant()
if ($commit -notmatch '^[0-9a-f]{40}$') { throw 'Candidate manifest has an invalid release_commit.' }
$shortCommit = $commit.Substring(0, 12)
$artifactPath = Join-Path $SourceRoot ([string]$candidate.artifact_relative_path)
$artifactPath = (Resolve-Path -LiteralPath $artifactPath).Path
$artifactSha256 = ([string]$candidate.sha256).Trim().ToLowerInvariant()
$actualArtifactSha256 = (Get-FileHash -LiteralPath $artifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($artifactSha256 -notmatch '^[0-9a-f]{64}$' -or $actualArtifactSha256 -ne $artifactSha256) {
    throw 'Production candidate artifact failed SHA-256 verification.'
}

$evidenceDir = Join-Path $SourceRoot "runtime\deploy\go_${stamp}_${shortCommit}"
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null
$candidateRoot = Join-Path $evidenceDir 'candidate_source'
Expand-Archive -LiteralPath $artifactPath -DestinationPath $candidateRoot
$task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction Stop
$taskInfo = Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName $TaskName
$originalAction = $task.Actions[0]
$principalBefore = $task.Principal.UserId
$currentPath = 'C:\dp_program\current'
$oldCurrentTarget = $null
if (Test-Path -LiteralPath $currentPath) {
    $currentItem = Get-Item -LiteralPath $currentPath -Force
    if (($currentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
        throw "Existing stable path is not a junction: $currentPath"
    }
    $oldCurrentTarget = [string]$currentItem.Target
}

# Phase 0: immutable pre-deploy evidence.  This intentionally names the real
# wrapper as Scheduled Task; no Windows Service/NSSM evidence is fabricated.
[ordered]@{
    captured_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    hostname = $env:COMPUTERNAME
    user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    source_root = $SourceRoot
    source_commit = $commit
    candidate_manifest = $CandidateManifest
    artifact_path = $artifactPath
    artifact_sha256 = $artifactSha256
    task_path = $TaskPath
    task_name = $TaskName
    task_state = [string]$task.State
    task_last_run = $taskInfo.LastRunTime.ToUniversalTime().ToString('o')
    task_last_result = $taskInfo.LastTaskResult
    task_principal = $principalBefore
    task_execute = $originalAction.Execute
    task_arguments = $originalAction.Arguments
    task_working_directory = $originalAction.WorkingDirectory
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDir 'predeploy_identity.json') -Encoding UTF8

$taskXmlText = Export-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
$taskXmlText | Set-Content -LiteralPath (Join-Path $evidenceDir 'scheduled_task_before.xml') -Encoding Unicode
if ($taskXmlText -notmatch '<Delay>PT45S</Delay>' -or
    $taskXmlText -notmatch '<Interval>PT1M</Interval>' -or
    $taskXmlText -notmatch '<Count>999</Count>') {
    throw 'Scheduled Task does not have the approved BootTrigger 45s / restart 1m x999 policy.'
}
Copy-Item -LiteralPath (Join-Path $SourceRoot 'config\dp_provider.env') `
    -Destination (Join-Path $evidenceDir 'dp_provider.env.before')

[ordered]@{
    release_commit = $commit
    artifact_path = $artifactPath
    expected_sha256 = $artifactSha256
    verified_sha256 = $actualArtifactSha256
    verified_at_utc = (Get-Date).ToUniversalTime().ToString('o')
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDir 'artifact_verification.json') -Encoding UTF8
try {
    Invoke-NativeChecked -Executable $ServicePython -Arguments @('-m', 'core_engine', 'doctor', '--json') `
        -WorkingDirectory $SourceRoot -EvidenceFile (Join-Path $evidenceDir 'doctor_before.json')
} catch { $_ | Out-String | Set-Content -LiteralPath (Join-Path $evidenceDir 'doctor_before_failure.txt') }
try {
    Invoke-NativeChecked -Executable $ServicePython -Arguments @('-m', 'core_engine', 'data-health', '--json') `
        -WorkingDirectory $SourceRoot -EvidenceFile (Join-Path $evidenceDir 'data_health_before.json')
} catch { $_ | Out-String | Set-Content -LiteralPath (Join-Path $evidenceDir 'data_health_before_failure.txt') }

# Full COPY_ONLY database backup includes SEN.ActiveTask, DWH.Fact_OHLCV and
# every DWH object.  VERIFYONLY WITH CHECKSUM is a hard gate before mutation.
$backupRootLines = & sqlcmd -S $SqlServer -E -d $Database -h -1 -W -Q `
    "SET NOCOUNT ON; SELECT CONVERT(nvarchar(4000), SERVERPROPERTY('InstanceDefaultBackupPath'));" 2>&1
if ($LASTEXITCODE -ne 0) { throw "Could not resolve SQL backup directory: $backupRootLines" }
$backupRoot = ($backupRootLines | Where-Object { $_ -and $_.Trim() } | Select-Object -First 1).Trim()
if (-not $backupRoot.EndsWith('\')) { $backupRoot += '\' }
$backupFile = $backupRoot + "SEN05_AutoTrading_GO_${stamp}_${shortCommit}.bak"
$escapedBackup = $backupFile.Replace("'", "''")
$backupQuery = @"
SET NOCOUNT ON;
BACKUP DATABASE [$Database] TO DISK = N'$escapedBackup'
  WITH COPY_ONLY, CHECKSUM, COMPRESSION, INIT, STATS = 10;
RESTORE VERIFYONLY FROM DISK = N'$escapedBackup' WITH CHECKSUM;
SELECT N'$escapedBackup' AS verified_backup_file;
"@
Invoke-NativeChecked -Executable 'sqlcmd' `
    -Arguments @('-b', '-r1', '-S', $SqlServer, '-E', '-d', $Database, '-Q', $backupQuery) `
    -WorkingDirectory $SourceRoot -EvidenceFile (Join-Path $evidenceDir 'database_backup_verify.log')

# Graceful stop only.  If the supervisor refuses to exit, abort without force
# killing it; destructive maintenance must not start while writers are alive.
try {
    Invoke-NativeChecked -Executable $ServicePython -Arguments @('-m', 'core_engine', 'stop', '--reason', 'production-go-deploy') `
        -WorkingDirectory $SourceRoot -EvidenceFile (Join-Path $evidenceDir 'graceful_stop.log')
} catch {
    # A previously failed supervisor can legitimately have nothing to stop.
    $_ | Out-String | Add-Content -LiteralPath (Join-Path $evidenceDir 'graceful_stop.log')
}
if (-not (Wait-TaskNotRunning -TimeoutSec 120)) {
    throw 'Scheduled Task/supervisor did not stop gracefully within 120 seconds; no migration was attempted.'
}

$migrationsStarted = $false
$promotionComplete = $false
try {
    $migrationsStarted = $true
    Invoke-SqlFileChecked -Path (Join-Path $candidateRoot 'scripts\sql\10_migration_usp_loaddirect_v3_date_fence.sql') `
        -EvidenceFile (Join-Path $evidenceDir 'migration_10_usp_v3.log') -Commit $commit
    Invoke-SqlFileChecked -Path (Join-Path $candidateRoot 'scripts\sql\09_migration_lock_fencing.sql') `
        -EvidenceFile (Join-Path $evidenceDir 'migration_09_lock_fencing.log') -Commit $commit
    Invoke-SqlFileChecked -Path (Join-Path $candidateRoot 'scripts\sql\11_migration_archive_us500_d1_unsupported_calendar.sql') `
        -EvidenceFile (Join-Path $evidenceDir 'migration_11_archive.log') -Commit $commit

    $contractQuery = @"
SET NOCOUNT ON;
SELECT
  CAST(ep.value AS varchar(20)) AS contract_version,
  (SELECT COUNT(*) FROM sys.columns WHERE object_id=OBJECT_ID('SEN.ActiveTask') AND name IN ('OwnerId','Fence')) AS lock_fencing_columns,
  (SELECT COUNT(*) FROM SEN.OHLCV_UnsupportedCalendar WHERE SourceTable='SEN.TF_D1' AND SymbolID=8 AND Reason='unsupported_calendar_date_before_2008') AS archived_unsupported_rows,
  (SELECT COUNT(*) FROM SEN.TF_D1 s LEFT JOIN DWH.Dim_Date d ON d.FullDate=CAST(s.BarTime AS date) WHERE s.SymbolID=8 AND d.DateKey IS NULL) AS unsupported_staging_rows
FROM sys.extended_properties ep
WHERE ep.major_id=OBJECT_ID('DWH.usp_LoadDirect') AND ep.minor_id=0 AND ep.name='DPContractVersion'
FOR JSON PATH, WITHOUT_ARRAY_WRAPPER;
"@
    Invoke-NativeChecked -Executable 'sqlcmd' `
        -Arguments @('-b', '-S', $SqlServer, '-E', '-d', $Database, '-h', '-1', '-W', '-w', '65535', '-Q', $contractQuery) `
        -WorkingDirectory $SourceRoot -EvidenceFile (Join-Path $evidenceDir 'database_contract.json')
    $contract = Get-Content -LiteralPath (Join-Path $evidenceDir 'database_contract.json') -Raw | ConvertFrom-Json
    if ([string]$contract.contract_version -ne '3' -or [int]$contract.lock_fencing_columns -ne 2 -or
        [int]$contract.archived_unsupported_rows -ne 2231 -or [int]$contract.unsupported_staging_rows -ne 0) {
        throw 'Database contract/archive postconditions failed.'
    }

    $previousPythonPath = $env:PYTHONPATH
    $previousAppRoot = $env:DP_APP_ROOT
    try {
        $env:PYTHONPATH = Join-Path $candidateRoot 'src'
        $env:DP_APP_ROOT = $SourceRoot
        & $ServicePython -m core_engine reconcile-fact --apply --json 2>&1 |
            Tee-Object -FilePath (Join-Path $evidenceDir 'reconcile_apply.json')
        $reconcileExit = $LASTEXITCODE
    }
    finally {
        $env:PYTHONPATH = $previousPythonPath
        $env:DP_APP_ROOT = $previousAppRoot
    }
    $reconcile = Get-Content -LiteralPath (Join-Path $evidenceDir 'reconcile_apply.json') -Raw | ConvertFrom-Json
    if ($reconcileExit -ne 0 -or [int]$reconcile.supported_missing_fact_rows -ne 0 -or
        [int]$reconcile.supported_mismatched_fact_rows -ne 0 -or
        [int]$reconcile.unsupported_calendar_rows -ne 0) {
        throw 'reconcile-fact did not reach all-zero supported/mismatched/unsupported staging acceptance.'
    }

    $watermarkQuery = "SET NOCOUNT ON; SELECT COUNT_BIG(*) AS fact_rows, CONVERT(varchar(33), MAX(BarTime), 127) AS max_bar_time_utc FROM DWH.Fact_OHLCV;"
    Invoke-NativeChecked -Executable 'sqlcmd' `
        -Arguments @('-b', '-S', $SqlServer, '-E', '-d', $Database, '-Q', $watermarkQuery) `
        -WorkingDirectory $SourceRoot -EvidenceFile (Join-Path $evidenceDir 'fact_watermark_before_start.log')

    Invoke-NativeChecked -Executable 'powershell.exe' `
        -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                     (Join-Path $candidateRoot 'scripts\windows_task\deploy_release.ps1'),
                     '-SourceRoot', $SourceRoot, '-TaskPath', $TaskPath,
                     '-TaskName', $TaskName, '-ArtifactPath', $artifactPath,
                     '-ArtifactSha256', $artifactSha256, '-ResolvedCommit', $commit,
                     '-ServicePython', $ServicePython, '-StartTask') `
        -WorkingDirectory $SourceRoot -EvidenceFile (Join-Path $evidenceDir 'release_deploy.log')

    $releasePython = 'C:\dp_program\current\.venv\Scripts\python.exe'
    $deadline = (Get-Date).AddSeconds(180)
    $runtimeHealthy = $false
    do {
        Start-Sleep -Seconds 5
        $taskNow = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
        $statePath = Join-Path $SourceRoot 'runtime\run\backend_engine_state.json'
        if ($taskNow.State -eq 'Running' -and (Test-Path -LiteralPath $statePath)) {
            try {
                $backend = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
                $runtimeHealthy = ($backend.status -eq 'running' -and [int]$backend.live_pid -gt 0)
            } catch { $runtimeHealthy = $false }
        }
    } while (-not $runtimeHealthy -and (Get-Date) -lt $deadline)
    if (-not $runtimeHealthy) { throw 'Release Task did not reach supervisor=running with a live child within 180 seconds.' }

    Invoke-NativeChecked -Executable $releasePython -Arguments @('-m', 'core_engine', 'doctor', '--json') `
        -WorkingDirectory 'C:\dp_program\current' -EvidenceFile (Join-Path $evidenceDir 'doctor_after.json')
    Invoke-NativeChecked -Executable $releasePython -Arguments @('-m', 'core_engine', 'settings', '--json') `
        -WorkingDirectory 'C:\dp_program\current' -EvidenceFile (Join-Path $evidenceDir 'settings_after.json')

    $settingsAfter = Get-Content -LiteralPath (Join-Path $evidenceDir 'settings_after.json') -Raw | ConvertFrom-Json
    if ([int]$settingsAfter.live_fetching.expected_live_symbols -ne 11 -or
        [int]$settingsAfter.live_fetching.resolved_live_symbols -ne 11 -or
        [int]$settingsAfter.live_fetching.symbol_timeframe_sessions -ne 165) {
        throw 'Post-start settings do not prove 11 live symbols / 165 sessions.'
    }

    Export-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName |
        Set-Content -LiteralPath (Join-Path $evidenceDir 'scheduled_task_after.xml') -Encoding Unicode
    $taskAfter = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
    if ($taskAfter.Principal.UserId -ne $principalBefore -or $taskAfter.State -ne 'Running') {
        throw 'Scheduled Task principal/state changed unexpectedly after promotion.'
    }

    $promotionComplete = $true
}
catch {
    $_ | Out-String | Set-Content -LiteralPath (Join-Path $evidenceDir 'promotion_failure.txt')
    if ($migrationsStarted) {
        # Reverse order rollback: stop new code, restore previous action/link,
        # restore the verified full backup, then restart the old Scheduled Task.
        try {
            $activePython = 'C:\dp_program\current\.venv\Scripts\python.exe'
            if (Test-Path -LiteralPath $activePython) {
                & $activePython -m core_engine stop --reason promotion-rollback 2>&1 |
                    Tee-Object -FilePath (Join-Path $evidenceDir 'rollback_graceful_stop.log')
                [void](Wait-TaskNotRunning -TimeoutSec 120)
            }
            Stop-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
            Set-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $originalAction | Out-Null

            if (Test-Path -LiteralPath $currentPath) { Remove-JunctionOnly $currentPath }
            if ($oldCurrentTarget) {
                New-Item -ItemType Junction -Path $currentPath -Target $oldCurrentTarget | Out-Null
            }

            $restoreQuery = @"
ALTER DATABASE [$Database] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
RESTORE DATABASE [$Database] FROM DISK = N'$escapedBackup' WITH REPLACE, CHECKSUM;
ALTER DATABASE [$Database] SET MULTI_USER;
"@
            Invoke-NativeChecked -Executable 'sqlcmd' `
                -Arguments @('-b', '-r1', '-S', $SqlServer, '-E', '-d', 'master', '-Q', $restoreQuery) `
                -WorkingDirectory $SourceRoot -EvidenceFile (Join-Path $evidenceDir 'rollback_database_restore.log')
            Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
            'rollback_complete' | Set-Content -LiteralPath (Join-Path $evidenceDir 'rollback_result.txt')
        }
        catch {
            $_ | Out-String | Set-Content -LiteralPath (Join-Path $evidenceDir 'rollback_failure.txt')
        }
    }
    throw
}

if ($promotionComplete) {
    [ordered]@{
        result = 'promotion_complete'
        commit = $commit
        evidence_directory = $evidenceDir
        database_backup = $backupFile
        scheduled_task = "$TaskPath$TaskName"
        completed_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    } | ConvertTo-Json
}
