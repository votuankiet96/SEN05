[CmdletBinding()]
param(
    [string]$ComputerName = "10.11.12.6",
    [string]$UserName = "Administrator",
    [Parameter(Mandatory = $true)]
    [string]$IdentityFile,
    [string]$RepoPath = "C:\Users\Administrator\Desktop\dp_program",
    [string]$PythonPath = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe",
    [string]$TaskPath = "\SEN05\",
    [string]$TaskName = "SEN05 DP Program 24x7",
    [ValidateRange(1, 168)]
    [int]$RiskHours = 24
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
$resolvedIdentity = (Resolve-Path -LiteralPath $IdentityFile -ErrorAction Stop).Path
$target = "$UserName@$ComputerName"

function Convert-ToSingleQuotedLiteral {
    param([Parameter(Mandatory = $true)][string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

$repoLiteral = Convert-ToSingleQuotedLiteral $RepoPath
$pythonLiteral = Convert-ToSingleQuotedLiteral $PythonPath
$taskPathLiteral = Convert-ToSingleQuotedLiteral $TaskPath
$taskNameLiteral = Convert-ToSingleQuotedLiteral $TaskName
$riskSinceLiteral = Convert-ToSingleQuotedLiteral ("{0}h" -f $RiskHours)

$remoteScript = @"
`$ErrorActionPreference = 'Continue'
`$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(`$false)

function Write-Section([string]`$Name) {
    Write-Output ('===== ' + `$Name + ' =====')
}

Write-Section 'snapshot'
[pscustomobject]@{
    hostname = `$env:COMPUTERNAME
    generated_at_utc = [DateTime]::UtcNow.ToString('o')
    repo_path = $repoLiteral
} | ConvertTo-Json -Depth 4

Write-Section 'host'
try {
    `$os = Get-CimInstance Win32_OperatingSystem
    `$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
    [pscustomobject]@{
        last_boot_utc = `$os.LastBootUpTime.ToUniversalTime().ToString('o')
        disk_free_gb = [math]::Round(`$disk.FreeSpace / 1GB, 2)
        disk_total_gb = [math]::Round(`$disk.Size / 1GB, 2)
    } | ConvertTo-Json
} catch {
    Write-Output ('host-check-failed: ' + `$_.Exception.Message)
}

Write-Section 'repository'
try {
    `$physical = Get-Item -LiteralPath $repoLiteral
    `$junction = Get-Item -LiteralPath 'C:\Share\dp_program' -ErrorAction SilentlyContinue
    `$headPath = Join-Path $repoLiteral '.git\HEAD'
    `$head = if (Test-Path -LiteralPath `$headPath) {
        (Get-Content -LiteralPath `$headPath -Raw).Trim()
    } else { `$null }
    `$commit = `$null
    if (`$head -like 'ref: *') {
        `$refPath = Join-Path (Join-Path $repoLiteral '.git') `$head.Substring(5)
        if (Test-Path -LiteralPath `$refPath) {
            `$commit = (Get-Content -LiteralPath `$refPath -Raw).Trim()
        }
    } elseif (`$head) {
        `$commit = `$head
    }
    [pscustomobject]@{
        physical_root = `$physical.FullName
        junction_root = if (`$junction) { `$junction.FullName } else { `$null }
        junction_target = if (`$junction) { (`$junction.Target -join ',') } else { `$null }
        git_head = `$head
        git_commit = `$commit
        agents_md = Test-Path -LiteralPath (Join-Path $repoLiteral 'AGENTS.md')
    } | ConvertTo-Json
} catch {
    Write-Output ('repository-check-failed: ' + `$_.Exception.Message)
}

Write-Section 'scheduled-task'
try {
    `$task = Get-ScheduledTask -TaskPath $taskPathLiteral -TaskName $taskNameLiteral
    `$info = Get-ScheduledTaskInfo -TaskPath $taskPathLiteral -TaskName $taskNameLiteral
    [pscustomobject]@{
        name = `$task.TaskName
        state = [string]`$task.State
        execute = `$task.Actions[0].Execute
        arguments = `$task.Actions[0].Arguments
        working_directory = `$task.Actions[0].WorkingDirectory
        account = `$task.Principal.UserId
        last_run_time = `$info.LastRunTime.ToUniversalTime().ToString('o')
        last_task_result = `$info.LastTaskResult
        next_run_time = if (`$info.NextRunTime) {
            `$info.NextRunTime.ToUniversalTime().ToString('o')
        } else { `$null }
    } | ConvertTo-Json
} catch {
    Write-Output ('scheduled-task-check-failed: ' + `$_.Exception.Message)
}

Write-Section 'processes'
try {
    Get-CimInstance Win32_Process |
        Where-Object {
            `$_.Name -match '^python(w)?\.exe$' -and
            `$_.CommandLine -match 'core_engine'
        } |
        Select-Object ProcessId, ParentProcessId, CreationDate, CommandLine |
        ConvertTo-Json -Depth 4
} catch {
    Write-Output ('process-check-failed: ' + `$_.Exception.Message)
}

if (-not (Test-Path -LiteralPath $pythonLiteral)) {
    Write-Section 'application'
    Write-Output 'python-not-found'
    exit 2
}
if (-not (Test-Path -LiteralPath $repoLiteral)) {
    Write-Section 'application'
    Write-Output 'repository-not-found'
    exit 2
}

Set-Location -LiteralPath $repoLiteral
`$commands = @(
    @{ Name = 'settings'; Args = @('-m', 'core_engine', 'settings', '--json') },
    @{ Name = 'doctor'; Args = @('-m', 'core_engine', 'doctor', '--json') },
    @{ Name = 'status'; Args = @('-m', 'core_engine', 'status', '--json') },
    @{ Name = 'data-health'; Args = @('-m', 'core_engine', 'data-health', '--json') },
    @{ Name = 'log-status'; Args = @('-m', 'core_engine', 'logs', 'status') },
    @{ Name = 'log-risks'; Args = @('-m', 'core_engine', 'logs', 'risks', '--since', $riskSinceLiteral) }
)

foreach (`$command in `$commands) {
    Write-Section `$command.Name
    & $pythonLiteral @(`$command.Args) 2>&1
    Write-Output ('exit_code=' + `$LASTEXITCODE)
}
"@

$encoded = [Convert]::ToBase64String(
    [Text.Encoding]::Unicode.GetBytes($remoteScript)
)
$remoteCommand = "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand $encoded"

& $ssh `
    "-i" $resolvedIdentity `
    "-o" "BatchMode=yes" `
    "-o" "ConnectTimeout=10" `
    $target `
    $remoteCommand

if ($LASTEXITCODE -ne 0) {
    throw "Remote evidence collection failed with SSH exit code $LASTEXITCODE."
}
