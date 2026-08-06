<#
.SYNOPSIS
  Stop and remove the SSMS POS sync Windows service.
#>
param(
    [string]$ServiceName = "SSMSPosSync",
    [string]$NssmPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

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
    throw "NSSM not found. Pass -NssmPath or add nssm.exe to PATH."
}

$nssm = Resolve-Nssm -Explicit $NssmPath
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $svc) {
    Write-Host "Service '$ServiceName' is not installed." -ForegroundColor Yellow
    return
}

& $nssm stop $ServiceName confirm
Start-Sleep -Seconds 2
& $nssm remove $ServiceName confirm
Write-Host "Removed service '$ServiceName'." -ForegroundColor Green
