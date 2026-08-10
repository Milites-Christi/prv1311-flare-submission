<#
Registers flare/rider_flare.py as a Windows Scheduled Task -- same shape as
install_rider_team_task.ps1:
  - starts automatically at boot, before any user logs in (Trigger: AtStartup)
  - runs as SYSTEM (LogonType ServiceAccount -- no stored password, no login required)
  - restarts automatically if the process ever exits (RestartCount/RestartInterval)

rider_flare.py calls rider_team.run_engine() directly (imported, not copied)
with a swapped-in FTSO price_fn, a fixed 16-symbol universe, and its own
ledger/state-table/log -- it is exactly as ledger-only as rider_team.py
itself (confirmed when rider_team.py was first scripted): pure in-memory
arithmetic against data/rider_flare_ledger.json (+ a separate Supabase
mirror, rider_flare_state). No exchange order calls anywhere in the chain.

Runs as `python -m flare.rider_flare`, NOT `python flare\rider_flare.py` --
module invocation is required so Python puts the working directory (not
flare\'s own directory) on sys.path, which is what lets rider_flare.py
`import rider_team` and `from flare.price_adapter import ...` resolve.
WorkingDirectory is therefore load-bearing here, not cosmetic.

Prerequisite: the oracle_divergence and rider_flare_state tables must exist
in Supabase (see flare/README.md / the SQL handed over alongside this
script) and the PostgREST schema cache must have been reloaded, or the
service's first ledger push will fail (silently logged, not fatal, but
useless) until that's done.

MUST be run from an elevated ("Run as Administrator") PowerShell:

    powershell -ExecutionPolicy Bypass -File "C:\Users\Autry\accum-flip\Prv1311\install_rider_flare_task.ps1"

Idempotent: safe to re-run. Unregisters any existing task with the same name first.
#>

$ErrorActionPreference = "Stop"

$TaskName   = "PRV1311-RiderTeamFlare"
$RepoDir    = "C:\Users\Autry\accum-flip\Prv1311"
$PythonExe  = Join-Path $RepoDir "venv\Scripts\python.exe"
$ScriptPath = Join-Path $RepoDir "flare\rider_flare.py"

if (-not (Test-Path $PythonExe)) {
    throw "venv python not found at $PythonExe -- check RepoDir."
}
if (-not (Test-Path $ScriptPath)) {
    throw "flare\rider_flare.py not found at $ScriptPath -- check RepoDir."
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Existing task '$TaskName' found -- stopping and unregistering before re-creating."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "-m flare.rider_flare" -WorkingDirectory $RepoDir
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
    -Description "PRV1311 Rider Team Flare twin (ledger-only paper trading, FTSO-priced). No exchange orders placed." | Out-Null

Write-Host "Task '$TaskName' registered."

# On some Windows builds, StartWhenAvailable causes Task Scheduler to treat
# the AtStartup trigger as "missed" the instant it's registered post-boot
# and auto-fire it immediately -- racing an unconditional Start-ScheduledTask
# call here and launching two processes despite MultipleInstances IgnoreNew.
# Two rider_flare.py processes would both read/write the same ledger file --
# a real lost-update race, not just noise. Checking .State first prevents it.
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
    Where-Object { $_.CommandLine -like "*flare.rider_flare*" } |
    Select-Object ProcessId, CommandLine | Format-List

Write-Host ""
Write-Host "Log file: $RepoDir\logs\rider_flare.log"
