<#
.SYNOPSIS
  Install the SSMS POS sync worker as a Windows service (NSSM).

.DESCRIPTION
  Requires:
    - .NET 8 SDK (to publish) or a pre-built publish folder with PosSyncService.exe
    - appsettings.json / appsettings.Production.json configured for this branch
    - NSSM on PATH, or pass -NssmPath

  Example:
    .\scripts\install_sync_service.ps1 `
      -ServiceDir "C:\apps\ssms\sync" `
      -Publish
#>
param(
    [string]$ServiceName = "SSMSPosSync",
    [string]$DisplayName = "SSMS POS Sync",
    [string]$ServiceDir = "",
    [string]$NssmPath = "",
    [string]$DotNetEnvironment = "Production",
    [switch]$Publish
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$projectDir = Join-Path $repoRoot "sync-service\PosSyncService"

if (-not $ServiceDir) {
    $ServiceDir = Join-Path $projectDir "publish"
}

function Resolve-Nssm {
    param([string]$Explicit)
    if ($Explicit) {
        if (-not (Test-Path $Explicit)) { throw "NSSM not found: $Explicit" }
        return (Resolve-Path $Explicit).Path
    }
    $cmd = Get-Command nssm -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @(
            "C:\nssm\nssm.exe",
            "C:\Tools\nssm\nssm.exe",
            "C:\Program Files\nssm\nssm.exe",
            (Join-Path $repoRoot "tools\nssm\nssm.exe")
        )) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw @"
NSSM not found. Download from https://nssm.cc/download
Extract nssm.exe and either:
  - add it to PATH, or
  - pass -NssmPath 'C:\path\to\nssm.exe'
"@
}

if ($Publish -or -not (Test-Path (Join-Path $ServiceDir "PosSyncService.exe"))) {
    Write-Host "Publishing PosSyncService to $ServiceDir..." -ForegroundColor Cyan
    if (-not (Test-Path $projectDir)) {
        throw "Project not found: $projectDir"
    }
    dotnet publish $projectDir -c Release -o $ServiceDir
    if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed ($LASTEXITCODE)" }
}

$exePath = Join-Path $ServiceDir "PosSyncService.exe"
if (-not (Test-Path $exePath)) {
    throw "Missing PosSyncService.exe at $exePath`nRun with -Publish or build the project first."
}

$nssm = Resolve-Nssm -Explicit $NssmPath

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Service '$ServiceName' already exists - updating configuration..." -ForegroundColor Yellow
    & $nssm stop $ServiceName confirm
    Start-Sleep -Seconds 2
}
else {
    Write-Host "Installing service '$ServiceName'..." -ForegroundColor Cyan
    & $nssm install $ServiceName $exePath
    if ($LASTEXITCODE -ne 0) { throw "nssm install failed ($LASTEXITCODE)" }
}

$logDir = Join-Path $ServiceDir "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

& $nssm set $ServiceName Application $exePath
& $nssm set $ServiceName AppDirectory $ServiceDir
& $nssm set $ServiceName DisplayName $DisplayName
& $nssm set $ServiceName Description "SSMS branch POS sync worker (sales + stock to portal API)"
& $nssm set $ServiceName Start SERVICE_AUTO_START
& $nssm set $ServiceName AppStdout (Join-Path $logDir "sync-stdout.log")
& $nssm set $ServiceName AppStderr (Join-Path $logDir "sync-stderr.log")
& $nssm set $ServiceName AppRotateFiles 1
& $nssm set $ServiceName AppRotateBytes 10485760
& $nssm set $ServiceName AppEnvironmentExtra "DOTNET_ENVIRONMENT=$DotNetEnvironment"

& $nssm start $ServiceName
Start-Sleep -Seconds 2
$svc = Get-Service -Name $ServiceName
Write-Host "Service status: $($svc.Status)" -ForegroundColor $(if ($svc.Status -eq 'Running') { 'Green' } else { 'Yellow' })
Write-Host "Sync worker directory: $ServiceDir" -ForegroundColor Green
Write-Host "Logs: $logDir" -ForegroundColor Green
