[CmdletBinding()]
param(
    [string]$SourceRoot = 'C:\Share\dp_program',
    [string]$ReleaseRoot = 'C:\dp_releases',
    [string]$StableRoot = 'C:\dp_program',
    [string]$TaskPath = '\SEN05\',
    [string]$TaskName = 'SEN05 DP Program 24x7',
    [string]$Commit = 'HEAD',
    [string]$ArtifactPath = '',
    [string]$ArtifactSha256 = '',
    [string]$ResolvedCommit = '',
    [string]$ServicePython = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe',
    [switch]$StartTask,
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'deploy_release.ps1 must run from an elevated PowerShell session on VM-DP6.'
    }
}

function Remove-JunctionOnly([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
        throw "Refusing to remove non-junction path: $Path"
    }
    Remove-Item -LiteralPath $Path -Force
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
        if ($LASTEXITCODE -ne 0) {
            throw "$Executable exited with code $LASTEXITCODE. Evidence: $EvidenceFile"
        }
    }
    finally {
        Pop-Location
    }
}

Assert-Administrator

$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
if (-not $ArtifactPath -and -not (Test-Path -LiteralPath (Join-Path $resolvedSource '.git'))) {
    throw "SourceRoot is not the dp-program git checkout: $resolvedSource"
}
if (-not (Test-Path -LiteralPath $ServicePython)) {
    throw "Service Python was not found: $ServicePython"
}

$task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction Stop
if ($task.State -eq 'Running') {
    throw 'Scheduled Task is still Running. Request a graceful core_engine stop before release switching.'
}

$artifactSource = $null
if ($ArtifactPath) {
    $artifactSource = (Resolve-Path -LiteralPath $ArtifactPath).Path
    $commitFull = $ResolvedCommit.Trim().ToLowerInvariant()
    if ($commitFull -notmatch '^[0-9a-f]{40}$') {
        throw 'ResolvedCommit must be the full 40-character commit for the prebuilt artifact.'
    }
    $actualArtifactHash = (Get-FileHash -LiteralPath $artifactSource -Algorithm SHA256).Hash.ToLowerInvariant()
    if (-not $ArtifactSha256 -or $actualArtifactHash -ne $ArtifactSha256.Trim().ToLowerInvariant()) {
        throw "Prebuilt artifact SHA-256 mismatch: $artifactSource"
    }
}
else {
    $commitFull = (& git -C $resolvedSource rev-parse "$Commit^{commit}").Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $commitFull -notmatch '^[0-9a-f]{40}$') {
        throw "Could not resolve a full git commit from: $Commit"
    }
}
$shortCommit = $commitFull.Substring(0, 12)
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')

New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
New-Item -ItemType Directory -Path $StableRoot -Force | Out-Null
$evidenceDir = Join-Path $resolvedSource "runtime\deploy\release_${shortCommit}_$stamp"
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null

$releaseName = "${shortCommit}_$stamp"
$buildingDir = Join-Path $ReleaseRoot ".building_$releaseName"
$releaseDir = Join-Path $ReleaseRoot $releaseName
$archivePath = Join-Path $ReleaseRoot ".building_$releaseName.zip"

if ((Test-Path -LiteralPath $buildingDir) -or (Test-Path -LiteralPath $releaseDir)) {
    throw "Release path already exists: $releaseDir"
}

New-Item -ItemType Directory -Path $buildingDir | Out-Null
try {
    if ($artifactSource) {
        Copy-Item -LiteralPath $artifactSource -Destination $archivePath
        [ordered]@{
            artifact = $artifactSource
            sha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
            release_commit = $commitFull
            verified_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDir 'artifact_verification.json') -Encoding UTF8
    }
    else {
        Invoke-NativeChecked -Executable 'git' `
            -Arguments @('-C', $resolvedSource, 'archive', '--format=zip', '--output', $archivePath, $commitFull) `
            -WorkingDirectory $resolvedSource -EvidenceFile (Join-Path $evidenceDir 'git_archive.log')
    }
    Expand-Archive -LiteralPath $archivePath -DestinationPath $buildingDir
    Remove-Item -LiteralPath $archivePath -Force

    # Executable code is immutable in C:\dp_releases.  Only operator config
    # and runtime state remain mutable in C:\Share\dp_program so existing
    # credentials, browser cache, outboxes, logs and spool survive switches.
    $releaseConfig = Join-Path $buildingDir 'config'
    if (Test-Path -LiteralPath $releaseConfig) {
        $resolvedBuilding = [IO.Path]::GetFullPath($buildingDir).TrimEnd('\') + '\'
        $resolvedConfig = [IO.Path]::GetFullPath($releaseConfig)
        if (-not $resolvedConfig.StartsWith($resolvedBuilding, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Unsafe release config path: $resolvedConfig"
        }
        Remove-Item -LiteralPath $releaseConfig -Recurse -Force
    }
    New-Item -ItemType Junction -Path $releaseConfig -Target (Join-Path $resolvedSource 'config') | Out-Null
    New-Item -ItemType Junction -Path (Join-Path $buildingDir 'runtime') -Target (Join-Path $resolvedSource 'runtime') | Out-Null

    $manifest = [ordered]@{
        release_commit = $commitFull
        release_directory = $releaseDir
        deployed_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        source_checkout = $resolvedSource
        package_install = 'non-editable, release-local virtualenv'
        mutable_config = (Join-Path $resolvedSource 'config')
        mutable_runtime = (Join-Path $resolvedSource 'runtime')
    }
    $manifestJson = $manifest | ConvertTo-Json
    [IO.File]::WriteAllText(
        (Join-Path $buildingDir 'RELEASE_MANIFEST.json'),
        $manifestJson,
        (New-Object Text.UTF8Encoding($false))
    )

    & $ServicePython -m venv (Join-Path $buildingDir '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the release virtualenv.' }
    $releasePython = Join-Path $buildingDir '.venv\Scripts\python.exe'

    Invoke-NativeChecked -Executable $releasePython -Arguments @('-m', 'pip', 'install', '--disable-pip-version-check', '.[redis,dev]') `
        -WorkingDirectory $buildingDir -EvidenceFile (Join-Path $evidenceDir 'pip_install.log')
    Invoke-NativeChecked -Executable $releasePython -Arguments @('-m', 'compileall', '-q', 'src') `
        -WorkingDirectory $buildingDir -EvidenceFile (Join-Path $evidenceDir 'compileall.log')

    if (-not $SkipTests) {
        Invoke-NativeChecked -Executable $releasePython -Arguments @('-m', 'pytest', '-q') `
            -WorkingDirectory $buildingDir -EvidenceFile (Join-Path $evidenceDir 'pytest.log')
    }

    # Capture doctor for evidence, but do not fail the build solely because
    # runtime-state checks report the intentionally stopped pre-switch Task.
    Push-Location $buildingDir
    try {
        & $releasePython -m core_engine doctor --no-db --json 2>&1 |
            Tee-Object -FilePath (Join-Path $evidenceDir 'doctor_no_db.json')
        $LASTEXITCODE | Set-Content -LiteralPath (Join-Path $evidenceDir 'doctor_no_db.exit_code.txt')
    }
    finally { Pop-Location }
    Invoke-NativeChecked -Executable $releasePython -Arguments @('-m', 'core_engine', 'settings', '--json') `
        -WorkingDirectory $buildingDir -EvidenceFile (Join-Path $evidenceDir 'settings.json')

    $settings = Get-Content -LiteralPath (Join-Path $evidenceDir 'settings.json') -Raw | ConvertFrom-Json
    if ([int]$settings.live_fetching.expected_live_symbols -ne 11 -or
        [int]$settings.live_fetching.resolved_live_symbols -ne 11 -or
        [int]$settings.live_fetching.symbol_timeframe_sessions -ne 165) {
        throw 'Release settings failed the approved 11-symbol/165-session drift guard.'
    }

    Invoke-NativeChecked -Executable $releasePython -Arguments @('-m', 'pip', 'freeze') `
        -WorkingDirectory $buildingDir -EvidenceFile (Join-Path $buildingDir 'RELEASE_REQUIREMENTS.txt')

    Move-Item -LiteralPath $buildingDir -Destination $releaseDir
}
catch {
    if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }
    if (Test-Path -LiteralPath $buildingDir) {
        $resolvedBuild = [IO.Path]::GetFullPath($buildingDir)
        $resolvedReleaseRoot = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\') + '\'
        if ($resolvedBuild.StartsWith($resolvedReleaseRoot, [StringComparison]::OrdinalIgnoreCase)) {
            foreach ($childName in @('config', 'runtime')) {
                $childPath = Join-Path $buildingDir $childName
                if (Test-Path -LiteralPath $childPath) {
                    $child = Get-Item -LiteralPath $childPath -Force
                    if (($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                        Remove-JunctionOnly $childPath
                    }
                }
            }
            Remove-Item -LiteralPath $buildingDir -Recurse -Force
        }
    }
    throw
}

$finalReleasePython = Join-Path $releaseDir '.venv\Scripts\python.exe'
Invoke-NativeChecked -Executable $finalReleasePython -Arguments @('-c', 'import core_engine; print(core_engine.__file__)') `
    -WorkingDirectory $releaseDir -EvidenceFile (Join-Path $evidenceDir 'final_release_import.log')

$current = Join-Path $StableRoot 'current'
$next = Join-Path $StableRoot 'current.next'
$previous = Join-Path $StableRoot 'current.previous'
$previousTarget = $null
$originalAction = $task.Actions[0]
$principalBefore = $task.Principal.UserId

Remove-JunctionOnly $next
Remove-JunctionOnly $previous
New-Item -ItemType Junction -Path $next -Target $releaseDir | Out-Null

if (Test-Path -LiteralPath $current) {
    $currentItem = Get-Item -LiteralPath $current -Force
    if (($currentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
        throw "Refusing to replace non-junction stable path: $current"
    }
    $previousTarget = [string]$currentItem.Target
    Rename-Item -LiteralPath $current -NewName 'current.previous'
}

try {
    Rename-Item -LiteralPath $next -NewName 'current'
    $stablePython = Join-Path $current '.venv\Scripts\python.exe'
    $newAction = New-ScheduledTaskAction -Execute $stablePython -Argument '-m core_engine run --live' -WorkingDirectory $current
    Set-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $newAction | Out-Null

    $taskAfter = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
    if ($taskAfter.Principal.UserId -ne $principalBefore) {
        throw 'Scheduled Task principal changed during action update; rolling back.'
    }
    Export-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName |
        Set-Content -LiteralPath (Join-Path $evidenceDir 'scheduled_task_after.xml') -Encoding Unicode

    if ($StartTask) {
        Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
    }
}
catch {
    try { Stop-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}
    try { Set-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $originalAction | Out-Null } catch {}
    Remove-JunctionOnly $current
    if (Test-Path -LiteralPath $previous) {
        Rename-Item -LiteralPath $previous -NewName 'current'
    }
    throw
}

Remove-JunctionOnly $previous

$result = [ordered]@{
    result = 'deployed'
    release_commit = $commitFull
    release_directory = $releaseDir
    stable_path = $current
    previous_release = $previousTarget
    scheduled_task = "$TaskPath$TaskName"
    scheduled_task_started = [bool]$StartTask
    evidence_directory = $evidenceDir
    completed_at_utc = (Get-Date).ToUniversalTime().ToString('o')
}
$result | ConvertTo-Json
