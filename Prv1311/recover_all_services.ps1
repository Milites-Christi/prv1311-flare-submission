<#
Recovery script: all 6 PRV1311 scheduled tasks were found unregistered from
Task Scheduler while their underlying processes were still running orphaned
(no RestartCount protection, no AtStartup trigger -- a crash or reboot
would silently lose the service). Cause unconfirmed.

Stops each orphaned process by exact CommandLine match (never a bare
`taskkill /IM python.exe`, which would kill unrelated Python processes on
this machine too), then re-runs every install script fresh so Task
Scheduler tracks all six again.

MUST be run from an elevated ("Run as Administrator") PowerShell -- same
requirement as every install/uninstall script in this repo.

    powershell -ExecutionPolicy Bypass -File "C:\Users\Autry\accum-flip\Prv1311\recover_all_services.ps1"
#>

$ErrorActionPreference = "Stop"
$RepoDir = "C:\Users\Autry\accum-flip\Prv1311"

$Patterns = @(
    "rider_team.py",
    "footprint_worker.py",
    "run_all.py",
    "flare.rider_flare",
    "flare.divergence_recorder",
    "solo_rider.py"
)

Write-Host "=== Step 1: stopping orphaned processes (no Task Scheduler tracking them) ==="
foreach ($pattern in $Patterns) {
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -like "*$pattern*" }
    if ($procs) {
        foreach ($p in $procs) {
            Write-Host "  Stopping PID $($p.ProcessId) ($pattern): $($p.CommandLine)"
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
    } else {
        Write-Host "  No running process found for $pattern (already stopped, or never started)."
    }
}

Write-Host ""
Write-Host "Waiting 5s for processes to fully exit..."
Start-Sleep -Seconds 5

Write-Host ""
Write-Host "=== Step 2: re-registering all six services ==="
$InstallScripts = @(
    "install_rider_team_task.ps1",
    "install_footprint_worker_task.ps1",
    "install_run_all_task.ps1",
    "install_rider_flare_task.ps1",
    "install_divergence_recorder_task.ps1",
    "install_solo_rider_task.ps1"
)
foreach ($script in $InstallScripts) {
    Write-Host ""
    Write-Host "--- Running $script ---"
    & (Join-Path $RepoDir $script)
}

Write-Host ""
Write-Host "=== Step 3: final verification ==="
Get-ScheduledTask | Where-Object { $_.TaskName -like "*PRV1311*" } | Select-Object TaskName, State
