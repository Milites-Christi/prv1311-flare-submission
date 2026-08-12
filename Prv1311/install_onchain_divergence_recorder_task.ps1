<#
Registers flare/onchain_divergence_recorder.py as a Windows Scheduled Task --
same shape as install_divergence_recorder_task.ps1:
  - starts automatically at boot, before any user logs in (Trigger: AtStartup)
  - runs as SYSTEM (LogonType ServiceAccount -- no stored password, no login required)
  - restarts automatically if the process ever exits (RestartCount/RestartInterval)

onchain_divergence_recorder.py is read-only against both price sources -- it
never touches any ledger, never places an order, never wires into
rider_team.py/rider_flare.py's gate chain (confirmed by grep, see
docs/CHANGELOG.md 2026-08-11). Its only write is an insert to Supabase's
onchain_divergence table. Same risk class as DivergenceRecorder -- lowest in
this repo.

Runs as `python -m flare.onchain_divergence_recorder`, NOT
`python flare\onchain_divergence_recorder.py` by path -- this is the fix
pattern that resolved the AnchorWriter sys.path trap (see
install_anchor_writer_task.ps1 and docs/CHANGELOG.md 2026-08-11: running by
path puts the script's OWN directory on sys.path instead of the working
directory, so `import rider_team` / `from flare.coingecko_adapter import
...` / `from supabase_client import ...` all fail before the logger even
exists -- the task registers fine but exits immediately every time,
LastTaskResult 1, no log, no process, and nothing about that failure is
visible except the exit code). Module invocation puts the WorkingDirectory
(Prv1311\, not flare\) on sys.path instead, which is what makes those
imports resolve. WorkingDirectory is therefore load-bearing here, not
cosmetic -- this script uses the same pattern already fixed into
install_divergence_recorder_task.ps1, install_rider_flare_task.ps1, and
install_anchor_writer_task.ps1. It does NOT use the older by-path pattern
still present in install_solo_rider_task.ps1 and the original
install_rider_team_task.ps1-style scripts predating that fix.

Prerequisite: the onchain_divergence table already exists in Supabase
(created 2026-08-11, public schema, RLS enabled, public-read policy, schema
cache reloaded -- verified live via a manual `python -m
flare.onchain_divergence_recorder` run that wrote real rows, see
docs/CHANGELOG.md 2026-08-11).

MUST be run from an elevated ("Run as Administrator") PowerShell:

    powershell -ExecutionPolicy Bypass -File "C:\Users\Autry\accum-flip\Prv1311\install_onchain_divergence_recorder_task.ps1"

Idempotent: safe to re-run. Unregisters any existing task with the same name first.
#>

$ErrorActionPreference = "Stop"

$TaskName   = "PRV1311-OnchainDivergenceRecorder"
$RepoDir    = "C:\Users\Autry\accum-flip\Prv1311"
$PythonExe  = Join-Path $RepoDir "venv\Scripts\python.exe"
$ScriptPath = Join-Path $RepoDir "flare\onchain_divergence_recorder.py"

if (-not (Test-Path $PythonExe)) {
    throw "venv python not found at $PythonExe -- check RepoDir."
}
if (-not (Test-Path $ScriptPath)) {
    throw "flare\onchain_divergence_recorder.py not found at $ScriptPath -- check RepoDir."
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Existing task '$TaskName' found -- stopping and unregistering before re-creating."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "-m flare.onchain_divergence_recorder" -WorkingDirectory $RepoDir
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
    -Description "PRV1311 on-chain divergence recorder (read-only CoinGecko-vs-on-chain-swap logger for FLR/FXRP). No ledger writes, no orders, no gate wiring." | Out-Null

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
    Where-Object { $_.CommandLine -like "*flare.onchain_divergence_recorder*" } |
    Select-Object ProcessId, CommandLine | Format-List

Write-Host ""
Write-Host "Log file: $RepoDir\logs\onchain_divergence_recorder.log"
Write-Host "Check rider_health (component='onchain_divergence_recorder') and the onchain_divergence table for the first post-registration cycle's results."
