<#
Registers run_all.py as a Windows Scheduled Task -- same shape as
install_rider_team_task.ps1 / install_footprint_worker_task.ps1:
  - starts automatically at boot, before any user logs in (Trigger: AtStartup)
  - runs as SYSTEM (LogonType ServiceAccount -- no stored password, no login required)
  - restarts automatically if the process ever exits (RestartCount/RestartInterval)

run_all.py drives 8 engines (SCAV, DOGS, CORE, MARKOV, EWMA, REGIME, CHEAP, OBI) as
threads in one process, 60s-staggered. rider_team.py and footprint_worker.py are
NOT here -- they run as their own separate services (PRV1311-RiderTeam,
PRV1311-FootprintWorker). This script only ever touches run_all.py.

MUST be run from an elevated ("Run as Administrator") PowerShell:

    powershell -ExecutionPolicy Bypass -File "C:\Users\Autry\accum-flip\Prv1311\install_run_all_task.ps1"

Idempotent: safe to re-run. Unregisters any existing task with the same name first.
#>

$ErrorActionPreference = "Stop"

$TaskName   = "PRV1311-RunAll"
$RepoDir    = "C:\Users\Autry\accum-flip\Prv1311"
$PythonExe  = Join-Path $RepoDir "venv\Scripts\python.exe"
$ScriptPath = Join-Path $RepoDir "run_all.py"

if (-not (Test-Path $PythonExe)) {
    throw "venv python not found at $PythonExe -- check RepoDir."
}
if (-not (Test-Path $ScriptPath)) {
    throw "run_all.py not found at $ScriptPath -- check RepoDir."
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Existing task '$TaskName' found -- stopping and unregistering before re-creating."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$ScriptPath`"" -WorkingDirectory $RepoDir
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
    -Description "PRV1311 Run All -- drives SCAV/DOGS/CORE/MARKOV/EWMA/REGIME/CHEAP/OBI as threads, 60s-staggered. rider_team.py and footprint_worker.py run as separate services, not here." | Out-Null

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
    Where-Object { $_.CommandLine -like "*run_all.py*" } |
    Select-Object ProcessId, CommandLine | Format-List

Write-Host ""
Write-Host "Log file: $RepoDir\logs\run_all.log"
