<#
Registers flare/divergence_recorder.py as a Windows Scheduled Task -- same
shape as install_rider_team_task.ps1:
  - starts automatically at boot, before any user logs in (Trigger: AtStartup)
  - runs as SYSTEM (LogonType ServiceAccount -- no stored password, no login required)
  - restarts automatically if the process ever exits (RestartCount/RestartInterval)

divergence_recorder.py is read-only against both price sources -- it never
touches any ledger, never places an order, and its only write is an insert
to Supabase's oracle_divergence table. Lowest-risk service in this repo.

Runs as `python -m flare.divergence_recorder`, NOT a direct script path --
module invocation is required so Python puts the working directory on
sys.path, which is what lets the script `from screener import exchange` and
`from flare.price_adapter import ...` resolve. WorkingDirectory is
therefore load-bearing here, not cosmetic.

Prerequisite: the oracle_divergence table must exist in Supabase (see
flare/README.md / the SQL handed over alongside this script) and the
PostgREST schema cache must have been reloaded, or every insert will fail
(logged, not fatal, but the whole point of this service is the data it
writes).

MUST be run from an elevated ("Run as Administrator") PowerShell:

    powershell -ExecutionPolicy Bypass -File "C:\Users\Autry\accum-flip\Prv1311\install_divergence_recorder_task.ps1"

Idempotent: safe to re-run. Unregisters any existing task with the same name first.
#>

$ErrorActionPreference = "Stop"

$TaskName   = "PRV1311-DivergenceRecorder"
$RepoDir    = "C:\Users\Autry\accum-flip\Prv1311"
$PythonExe  = Join-Path $RepoDir "venv\Scripts\python.exe"
$ScriptPath = Join-Path $RepoDir "flare\divergence_recorder.py"

if (-not (Test-Path $PythonExe)) {
    throw "venv python not found at $PythonExe -- check RepoDir."
}
if (-not (Test-Path $ScriptPath)) {
    throw "flare\divergence_recorder.py not found at $ScriptPath -- check RepoDir."
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Existing task '$TaskName' found -- stopping and unregistering before re-creating."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "-m flare.divergence_recorder" -WorkingDirectory $RepoDir
# -RandomDelay: this script's import chain pulls in flare.price_adapter, which makes a
# LIVE Flare-mainnet RPC call at Python import time. Measured at ~20s even on a
# healthy, long-booted network (2026-08-11) -- a plausible failure point in the first
# moments after a fresh boot, before DNS/network/this machine's AV TLS-interception
# layer are ready (same class of issue that produced anchor_writer's exit-78 incident
# 2026-08-11; see docs/CHANGELOG.md 2026-08-12). This delay avoids racing that window;
# it doesn't fix the underlying import-time network dependency.
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Trigger.Delay = "PT2M"
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Principal $Principal -Settings $Settings `
    -Description "PRV1311 Flare divergence recorder (read-only FTSO-vs-Coinbase logger). No ledger writes, no orders." | Out-Null

Write-Host "Task '$TaskName' registered."

# On some Windows builds, StartWhenAvailable causes Task Scheduler to treat
# the AtStartup trigger as "missed" the instant it's registered post-boot
# and auto-fire it immediately -- racing an unconditional Start-ScheduledTask
# call here and launching two processes despite MultipleInstances IgnoreNew.
# Checking .State first avoids starting a second instance on top of that.
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
    Where-Object { $_.CommandLine -like "*flare.divergence_recorder*" } |
    Select-Object ProcessId, CommandLine | Format-List

Write-Host ""
Write-Host "Log file: $RepoDir\logs\divergence_recorder.log"
