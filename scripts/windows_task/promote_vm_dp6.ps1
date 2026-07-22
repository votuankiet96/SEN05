[CmdletBinding()]
param(
    [string]$SourceRoot = 'C:\Share\dp_program',
    [string]$TaskPath = '\SEN05\',
    [string]$TaskName = 'SEN05 DP Program 24x7',
    [string]$SqlServer = 'localhost',
    [string]$Database = 'SEN05_AutoTrading',
    [string]$BackupDirectory = '',
    [switch]$SkipDatabaseBackup,
    [string]$BackupWaiverReason = '',
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
if ($SkipDatabaseBackup -and [string]::IsNullOrWhiteSpace($BackupWaiverReason)) {
    throw '-SkipDatabaseBackup requires a non-empty -BackupWaiverReason for deployment evidence.'
}
if ($SkipDatabaseBackup -and $BackupDirectory) {
    throw '-SkipDatabaseBackup and -BackupDirectory cannot be used together.'
}
$backupPolicy = if ($SkipDatabaseBackup) {
    'skipped_by_explicit_operator_waiver'
} else {
    'full_copy_only_checksum_verified'
}
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
$sqlHelper = Join-Path $candidateRoot 'scripts\windows_task\sql_deploy.py'

function Set-Phase([string]$Name) {
    $payload = [ordered]@{
        phase = $Name
        updated_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        pid = $PID
    }
    $payload | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDir 'current_phase.json') -Encoding UTF8
    ($payload | ConvertTo-Json -Compress) | Add-Content -LiteralPath (Join-Path $evidenceDir 'phase_history.jsonl') -Encoding UTF8
    Write-Host "`n=== DP GO PHASE: $Name ===" -ForegroundColor Cyan
}

Set-Phase 'predeploy_evidence'
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
    database_backup_policy = $backupPolicy
    database_backup_waiver_reason = $BackupWaiverReason
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
Set-Phase 'database_backup_and_verify'
$backupManifestPath = Join-Path $evidenceDir 'database_backup_manifest.json'
$backupFile = $null
if ($SkipDatabaseBackup) {
    $backupManifest = [ordered]@{
        result = 'skipped_by_explicit_operator_waiver'
        reason = $BackupWaiverReason
        consequence = 'No full-database restore point; failed post-migration promotion leaves the Scheduled Task stopped for controlled recovery.'
        authorized_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    }
    $backupManifest | ConvertTo-Json | Set-Content -LiteralPath $backupManifestPath -Encoding UTF8
    $backupManifest | ConvertTo-Json | Tee-Object -FilePath (Join-Path $evidenceDir 'database_backup_verify.log')
} else {
    $backupArguments = @($sqlHelper, 'backup', '--server', $SqlServer, '--database', $Database,
                         '--stamp', $stamp, '--short-commit', $shortCommit,
                         '--output', $backupManifestPath)
    if ($BackupDirectory) {
        $backupArguments += @('--backup-directory', $BackupDirectory)
    }
    Invoke-NativeChecked -Executable $ServicePython `
        -Arguments $backupArguments `
        -WorkingDirectory $SourceRoot -EvidenceFile (Join-Path $evidenceDir 'database_backup_verify.log')
    $backupManifest = Get-Content -LiteralPath $backupManifestPath -Raw | ConvertFrom-Json
    if ($backupManifest.result -ne 'verified' -or -not $backupManifest.backup_file) {
        throw 'Python SQL helper did not produce a verified backup manifest.'
    }
    $backupFile = [string]$backupManifest.backup_file
}

# Graceful stop only.  If the supervisor refuses to exit, abort without force
# killing it; destructive maintenance must not start while writers are alive.
Set-Phase 'graceful_stop'
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
    Set-Phase 'database_migrations'
    $migrationManifestPath = Join-Path $evidenceDir 'migration_manifest.json'
    Invoke-NativeChecked -Executable $ServicePython `
        -Arguments @($sqlHelper, 'migrate', '--server', $SqlServer, '--database', $Database,
                     '--commit', $commit, '--files',
                     (Join-Path $candidateRoot 'scripts\sql\12_migration_usp_loaddirect_v4_bounded_plan.sql'),
                     (Join-Path $candidateRoot 'scripts\sql\09_migration_lock_fencing.sql'),
                     (Join-Path $candidateRoot 'scripts\sql\11_migration_archive_us500_d1_unsupported_calendar.sql'),
                     '--output', $migrationManifestPath) `
        -WorkingDirectory $SourceRoot -EvidenceFile (Join-Path $evidenceDir 'database_migrations.log')

    Set-Phase 'database_contract_verification'
    $contractPath = Join-Path $evidenceDir 'database_contract.json'
    Invoke-NativeChecked -Executable $ServicePython `
        -Arguments @($sqlHelper, 'contract', '--server', $SqlServer, '--database', $Database,
                     '--output', $contractPath) `
        -WorkingDirectory $SourceRoot -EvidenceFile (Join-Path $evidenceDir 'database_contract.log')
    $contract = Get-Content -LiteralPath $contractPath -Raw | ConvertFrom-Json
    if ([string]$contract.contract_version -ne '4' -or [int]$contract.lock_fencing_columns -ne 2 -or
        [int]$contract.archived_unsupported_rows -ne 2231 -or [int]$contract.unsupported_staging_rows -ne 0) {
        throw 'Database contract/archive postconditions failed.'
    }

    Set-Phase 'reconcile_fact'
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

    Set-Phase 'fact_watermark_before_start'
    Invoke-NativeChecked -Executable $ServicePython `
        -Arguments @($sqlHelper, 'watermark', '--server', $SqlServer, '--database', $Database,
                     '--output', (Join-Path $evidenceDir 'fact_watermark_before_start.json')) `
        -WorkingDirectory $SourceRoot -EvidenceFile (Join-Path $evidenceDir 'fact_watermark_before_start.log')

    Set-Phase 'immutable_release_deploy'
    Invoke-NativeChecked -Executable 'powershell.exe' `
        -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                     (Join-Path $candidateRoot 'scripts\windows_task\deploy_release.ps1'),
                     '-SourceRoot', $SourceRoot, '-TaskPath', $TaskPath,
                     '-TaskName', $TaskName, '-ArtifactPath', $artifactPath,
                     '-ArtifactSha256', $artifactSha256, '-ResolvedCommit', $commit,
                     '-ServicePython', $ServicePython, '-StartTask') `
        -WorkingDirectory $SourceRoot -EvidenceFile (Join-Path $evidenceDir 'release_deploy.log')

    Set-Phase 'scheduled_task_start_verification'
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
    Set-Phase 'promotion_complete'
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

            if ($backupFile) {
                Set-Phase 'rollback_database_restore'
                Invoke-NativeChecked -Executable $ServicePython `
                    -Arguments @($sqlHelper, 'restore', '--server', $SqlServer, '--database', $Database,
                                 '--backup-file', $backupFile) `
                    -WorkingDirectory $SourceRoot -EvidenceFile (Join-Path $evidenceDir 'rollback_database_restore.log')
                Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
                'rollback_complete' | Set-Content -LiteralPath (Join-Path $evidenceDir 'rollback_result.txt')
            } else {
                Set-Phase 'rollback_database_restore_skipped'
                [ordered]@{
                    result = 'database_restore_unavailable'
                    reason = $BackupWaiverReason
                    scheduled_task = 'left_stopped_to_prevent_old_code_running_against_migrated_schema'
                    recorded_at_utc = (Get-Date).ToUniversalTime().ToString('o')
                } | ConvertTo-Json | Set-Content `
                    -LiteralPath (Join-Path $evidenceDir 'rollback_result.txt') -Encoding UTF8
            }
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
        database_backup_policy = $backupPolicy
        database_backup_waiver_reason = $BackupWaiverReason
        scheduled_task = "$TaskPath$TaskName"
        completed_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    } | ConvertTo-Json
}
