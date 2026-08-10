<#
Registers solo_rider.py (run_service(), the multi-user PAPER-trading
service) as a Windows Scheduled Task -- same shape as
install_rider_team_task.ps1:
  - starts automatically at boot, before any user logs in (Trigger: AtStartup)
  - runs as SYSTEM (LogonType ServiceAccount -- no stored password, no login required)
  - restarts automatically if the process ever exits (RestartCount/RestartInterval)

PAPER TRADING ONLY. solo_rider.py has no real order-execution code anywhere
-- open_rider()/close_rider() (called via rider_team.run_cycle()) are pure
in-memory arithmetic against a JSON ledger. No exchange.create_order() call
exists in this file or in rider_team.py. Confirmed by reading both files
before writing this script, same as rider_team.py's own install script did.

Runs `python solo_rider.py` with NO arguments and NO SOLO_RIDER_USER_ID set
-- per solo_rider.py's own __main__ block, that specifically selects
run_service() (the shared multi-user service), not run_for_user() (the
dedicated single-user mode). Do not set SOLO_RIDER_USER_ID on this task; if
you ever do, it silently switches to single-user mode for that one user
only.

Prerequisite: solo_rider_config and solo_rider_state must exist in Supabase
(they did not as of this script being written -- see the CREATE TABLE SQL
handed over alongside it) with RLS + a public-read policy, and the
PostgREST schema cache must have been reloaded, or intake and every ledger
push will fail (logged, not fatal, but the service will look alive while
doing nothing useful).

MUST be run from an elevated ("Run as Administrator") PowerShell --
schtasks / Register-ScheduledTask does NOT self-elevate:

    powershell -ExecutionPolicy Bypass -File "C:\Users\Autry\accum-flip\Prv1311\install_solo_rider_task.ps1"

Idempotent: safe to re-run. Unregisters any existing task with the same name first.
#>

$ErrorActionPreference = "Stop"

$TaskName   = "PRV1311-SoloRider"
$RepoDir    = "C:\Users\Autry\accum-flip\Prv1311"
$PythonExe  = Join-Path $RepoDir "venv\Scripts\python.exe"
$ScriptPath = Join-Path $RepoDir "solo_rider.py"

if (-not (Test-Path $PythonExe)) {
    throw "venv python not found at $PythonExe -- check RepoDir."
}
if (-not (Test-Path $ScriptPath)) {
    throw "solo_rider.py not found at $ScriptPath -- check RepoDir."
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
    -Description "PRV1311 Solo Rider service -- PAPER TRADING ONLY. No exchange orders placed anywhere in this codebase." | Out-Null

Write-Host "Task '$TaskName' registered."

# On some Windows builds, StartWhenAvailable causes Task Scheduler to treat
# the AtStartup trigger as "missed" the instant it's registered post-boot
# and auto-fire it immediately -- racing an unconditional Start-ScheduledTask
# call here. Checking .State first avoids starting a second instance on
# top of that (same fix applied to the divergence-recorder/rider-flare
# install scripts after that exact race was caught live).
Start-Sleep -Seconds 2
$state = (Get-ScheduledTask -TaskName $TaskName).State
if ($state -eq 'Running') {
    Write-Host "Task already running (auto-started on registration) -- not starting it again."
} else {
    Write-Host "Starting it now so verification doesn't have to wait for a reboot..."
    Start-ScheduledTask -TaskName $TaskName
}

Start-Sleep -Seconds 3
Write-Host ""
Write-Host "--- Task status ---"
Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo |
    Format-List TaskName, LastRunTime, LastTaskResult, NextRunTime

Write-Host ""
Write-Host "--- Confirm the process is actually running ---"
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*solo_rider.py*" } |
    Select-Object ProcessId, CommandLine | Format-List

Write-Host ""
Write-Host "Log file: $RepoDir\logs\solo_rider.log"
Write-Host "Reminder: PAPER TRADING ONLY. No real orders are ever placed by this service."
