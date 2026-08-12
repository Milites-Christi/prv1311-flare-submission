<#
Registers flare/anchor_writer.py as a Windows Scheduled Task -- same shape as
install_solo_rider_task.ps1 / install_rider_team_task.ps1:
  - starts automatically at boot, before any user logs in (Trigger: AtStartup)
  - runs as SYSTEM (LogonType ServiceAccount -- no stored password, no login required)
  - restarts automatically if the process ever exits (RestartCount/RestartInterval)

REAL MONEY, NOT PAPER. Unlike every other service in this repo, this one
sends real signed transactions on Flare MAINNET and spends real FLR on gas.
It is bounded by mechanical ceilings in the script itself (MAX_CALLS_PER_RUN,
MAX_FLR_PER_DAY, MIN_WALLET_BALANCE, chain-id refusal -- see the module
docstring in flare/anchor_writer.py), never by a human confirmation prompt,
because a Windows service has no console to prompt.

THIS SCRIPT AUTO-STARTS THE TASK ON REGISTRATION, same as every other
install script here does (so verification doesn't have to wait for a
reboot) -- which means running this elevated command WILL trigger a real
anchoring cycle (up to 5 mainnet transactions, ~0.09 FLR each at today's gas
price) within seconds of you running it. That is not a bug in this script;
it is exactly what registering the task means. Confirm the wallet balance
and DIVERGENCE_ANCHOR_ADDRESS_MAINNET in .env are what you expect before
running this. Cadence is 8h (see flare/anchor_writer.py CYCLE_SEC).

Runs as `python -m flare.anchor_writer`, NOT `python flare\anchor_writer.py`
by path -- module invocation is required so Python puts the working
directory (not flare\'s own directory) on sys.path, which is what lets the
script `import rider_team` and `from supabase_client import ...` resolve.
WorkingDirectory is therefore load-bearing here, not cosmetic -- same
pattern already used by install_divergence_recorder_task.ps1 and
install_rider_flare_task.ps1. The first version of this script ran the
script by path; the task registered fine but exited immediately every time
(LastTaskResult 1, no log, no process) because every internal import failed
before the logger existed. Fixed here; see docs/CHANGELOG.md.

Prerequisite: the anchor_log Supabase table must already exist (it does as
of this script being written -- verified with a live insert/delete test,
schema cache reloaded).

MUST be run from an elevated ("Run as Administrator") PowerShell --
schtasks / Register-ScheduledTask does NOT self-elevate:

    powershell -ExecutionPolicy Bypass -File "C:\Users\Autry\accum-flip\Prv1311\install_anchor_writer_task.ps1"

Idempotent: safe to re-run. Unregisters any existing task with the same name first.
#>

$ErrorActionPreference = "Stop"

$TaskName   = "PRV1311-AnchorWriter"
$RepoDir    = "C:\Users\Autry\accum-flip\Prv1311"
$PythonExe  = Join-Path $RepoDir "venv\Scripts\python.exe"
$ScriptPath = Join-Path $RepoDir "flare\anchor_writer.py"

if (-not (Test-Path $PythonExe)) {
    throw "venv python not found at $PythonExe -- check RepoDir."
}
if (-not (Test-Path $ScriptPath)) {
    throw "flare\anchor_writer.py not found at $ScriptPath -- check RepoDir."
}

Write-Host "!!! This service sends REAL transactions on Flare MAINNET and spends REAL FLR. !!!"
Write-Host "Registering will auto-start it within seconds (see script header for why)." -ForegroundColor Yellow
Write-Host ""

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Existing task '$TaskName' found -- stopping and unregistering before re-creating."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "-m flare.anchor_writer" -WorkingDirectory $RepoDir
# -RandomDelay: this script's import chain (flare.deploy_anchor -> flare.price_adapter)
# makes a LIVE Flare-mainnet RPC call at Python import time, before the logger even
# exists (see the pre-logger guard at the top of anchor_writer.py). Measured at ~20s
# even on a healthy, long-booted network (2026-08-11) -- in the first moments after a
# fresh boot, before DNS/network/this machine's AV TLS-interception layer are ready,
# that same call is a plausible cause of the exit-78 failure observed 2026-08-11 at
# boot (see docs/CHANGELOG.md 2026-08-12; root cause traced, not proven -- the actual
# historical exception was unrecoverable, event logging was off). This delay doesn't
# fix the underlying import-time network dependency, it just avoids racing it during
# the one window most likely to lose that race.
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
    -Description "PRV1311 Flare mainnet DivergenceAnchor writer -- anchors every 8h, REAL FLR spend, mechanically capped (see flare/anchor_writer.py)." | Out-Null

Write-Host "Task '$TaskName' registered."

# On some Windows builds, StartWhenAvailable causes Task Scheduler to treat
# the AtStartup trigger as "missed" the instant it's registered post-boot
# and auto-fire it immediately -- racing an unconditional Start-ScheduledTask
# call here. Checking .State first avoids starting a second instance on top
# of that (same fix applied to every other install script in this repo
# after that exact race was caught live).
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
    Where-Object { $_.CommandLine -like "*flare.anchor_writer*" } |
    Select-Object ProcessId, CommandLine | Format-List

Write-Host ""
Write-Host "Log file: $RepoDir\logs\anchor_writer.log"
Write-Host "Check rider_health (component='anchor_writer') and the anchor_log table for the first cycle's results."
