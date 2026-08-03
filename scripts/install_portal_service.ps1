<#
.SYNOPSIS
  Install the SSMS Django portal as a Windows service (Waitress via NSSM).

.DESCRIPTION
  Requires:
    - Python venv already created and requirements installed
    - portal\.env.production configured
    - NSSM on PATH, or pass -NssmPath

  Example:
    .\scripts\install_portal_service.ps1 `
      -PortalDir "C:\apps\ssms\portal" `
      -PythonExe "C:\apps\ssms\.venv\Scripts\python.exe"
#>
param(
    [string]$ServiceName = "SSMSPortal",
    [string]$DisplayName = "SSMS Portal (Waitress)",
    [string]$PortalDir = "",
    [string]$PythonExe = "",
    [string]$NssmPath = "",
    [string]$WaitressHost = "0.0.0.0",
    [int]$WaitressPort = 2023,
    [int]$Threads = 6
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $PortalDir) {
    $PortalDir = Join-Path $repoRoot "portal"
}
if (-not $PythonExe) {
    $PythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
}

$runScript = Join-Path $PortalDir "run_waitress.py"
$envFile = Join-Path $PortalDir ".env.production"

if (-not (Test-Path $PythonExe)) {
    throw "Python not found: $PythonExe`nCreate a venv and install requirements first."
}
if (-not (Test-Path $runScript)) {
    throw "Missing run_waitress.py at $runScript"
}
if (-not (Test-Path $envFile)) {
    throw "Missing $envFile`nCopy .env.production.example and fill in production values."
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

$nssm = Resolve-Nssm -Explicit $NssmPath

# Prepare static files + migrate (safe to re-run)
Write-Host "Collecting static files..." -ForegroundColor Cyan
$env:APP_ENV = "production"
Push-Location $PortalDir
try {
    & $PythonExe manage.py migrate --noinput
    & $PythonExe manage.py collectstatic --noinput
}
finally {
    Pop-Location
}

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Service '$ServiceName' already exists — updating configuration..." -ForegroundColor Yellow
    & $nssm stop $ServiceName confirm
    Start-Sleep -Seconds 2
}
else {
    Write-Host "Installing service '$ServiceName'..." -ForegroundColor Cyan
    & $nssm install $ServiceName $PythonExe "`"$runScript`""
    if ($LASTEXITCODE -ne 0) { throw "nssm install failed ($LASTEXITCODE)" }
}

& $nssm set $ServiceName Application $PythonExe
& $nssm set $ServiceName AppParameters "`"$runScript`""
& $nssm set $ServiceName AppDirectory $PortalDir
& $nssm set $ServiceName DisplayName $DisplayName
& $nssm set $ServiceName Description "Supplier Stock Monitoring System web portal (Django + Waitress)"
& $nssm set $ServiceName Start SERVICE_AUTO_START
& $nssm set $ServiceName AppStdout (Join-Path $PortalDir "logs\waitress-stdout.log")
& $nssm set $ServiceName AppStderr (Join-Path $PortalDir "logs\waitress-stderr.log")
& $nssm set $ServiceName AppRotateFiles 1
& $nssm set $ServiceName AppRotateBytes 10485760
& $nssm set $ServiceName AppEnvironmentExtra "APP_ENV=production`0WAITRESS_HOST=$WaitressHost`0WAITRESS_PORT=$WaitressPort`0WAITRESS_THREADS=$Threads"

$logDir = Join-Path $PortalDir "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

& $nssm start $ServiceName
Start-Sleep -Seconds 2
$svc = Get-Service -Name $ServiceName
Write-Host "Service status: $($svc.Status)" -ForegroundColor $(if ($svc.Status -eq 'Running') { 'Green' } else { 'Yellow' })
Write-Host "Portal URL: http://localhost:$WaitressPort/" -ForegroundColor Green
Write-Host "Logs: $logDir" -ForegroundColor Green
