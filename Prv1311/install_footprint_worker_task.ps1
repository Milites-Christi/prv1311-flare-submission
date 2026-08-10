<#
Registers footprint_worker.py as a Windows Scheduled Task:
  - starts automatically at boot, before any user logs in (Trigger: AtStartup)
  - runs as SYSTEM (LogonType ServiceAccount -- no stored password, no login required)
  - restarts automatically if the process ever exits (RestartCount/RestartInterval)

MUST be run from an elevated ("Run as Administrator") PowerShell:

    powershell -ExecutionPolicy Bypass -File "C:\Users\Autry\accum-flip\Prv1311\install_footprint_worker_task.ps1"

Idempotent: safe to re-run. Unregisters any existing task with the same name first.
#>

$ErrorActionPreference = "Stop"

$TaskName   = "PRV1311-FootprintWorker"
$RepoDir    = "C:\Users\Autry\accum-flip\Prv1311"
$PythonExe  = Join-Path $RepoDir "venv\Scripts\python.exe"
$ScriptPath = Join-Path $RepoDir "footprint_worker.py"

# Staged rollout: 20 symbols, not the full 60-symbol watchlist. Expand after
# 48h clean per the plan. --timeframe is intentionally omitted -- the script
# defaults to footprint_gate.TIMEFRAME_S itself, so there's nothing here to
# drift out of sync.
$Products = @(
    "BTC-USD","ETH-USD","SOL-USD","XRP-USD","LINK-USD","AAVE-USD","UNI-USD",
    "ONDO-USD","AVAX-USD","NEAR-USD","LDO-USD","COTI-USD","EUL-USD","KAITO-USD",
    "HBAR-USD","FLR-USD","ADA-USD","XLM-USD","ARB-USD","OP-USD"
)

if (-not (Test-Path $PythonExe)) {
    throw "venv python not found at $PythonExe -- check RepoDir."
}
if (-not (Test-Path $ScriptPath)) {
    throw "footprint_worker.py not found at $ScriptPath -- check RepoDir."
}

$ArgList = @($ScriptPath) + $Products
$ArgumentString = ($ArgList | ForEach-Object { '"' + $_ + '"' }) -join ' '

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Existing task '$TaskName' found -- stopping and unregistering before re-creating."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument $ArgumentString -WorkingDirectory $RepoDir
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Principal $Principal -Settings $Settings `
    -Description "PRV1311 Coinbase footprint/order-flow ingestion worker. Feeds footprint_gate.py's veto data; the flow gate is BLIND_NO_DATA/stale without it." | Out-Null

Write-Host "Task '$TaskName' registered."
Write-Host "Starting it now so verification doesn't have to wait for a reboot..."
Start-ScheduledTask -TaskName $TaskName

Start-Sleep -Seconds 3
Write-Host ""
Write-Host "--- Task status ---"
Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo |
    Format-List TaskName, LastRunTime, LastTaskResult, NextRunTime

Write-Host ""
Write-Host "--- Confirm the process is actually running ---"
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*footprint_worker.py*" } |
    Select-Object ProcessId, CommandLine | Format-List

Write-Host ""
Write-Host "Log file: $RepoDir\logs\footprint_worker.log"
