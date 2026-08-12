<#
Re-registers every PRV1311 Windows Scheduled Task that currently exists as an
install_*.ps1 script in this repo (confirmed live via Get-ChildItem
2026-08-12 -- exactly 8, not assumed):

  PRV1311-FootprintWorker
  PRV1311-RiderTeam
  PRV1311-RiderTeamFlare
  PRV1311-DivergenceRecorder
  PRV1311-OnchainDivergenceRecorder
  PRV1311-SoloRider
  PRV1311-RunAll
  PRV1311-AnchorWriter          <-- REAL MONEY, see banner below

Each install script is independently idempotent (unregisters any existing
task with the same name first, then re-creates it) and auto-starts its
service within a few seconds of registration, same as every other run of
these scripts.

ANCHOR WRITER SPENDS REAL FLR ON REGISTRATION. Registering it auto-starts a
live mainnet cycle within seconds -- up to 5 real recordDivergence()
transactions, ~0.09-0.17 FLR each, gated only by the mechanical ceilings in
flare/anchor_writer.py (chain-id check, MIN_WALLET_BALANCE=2.0,
MAX_FLR_PER_DAY=1.8 -- confirmed restored to this value 2026-08-11). It is
run LAST in this script, after every other (free) service, and behind an
explicit typed confirmation so it cannot fire by accident from a fast
copy-paste-run.

MUST be run from an ELEVATED ("Run as Administrator") PowerShell:

    powershell -ExecutionPolicy Bypass -File "C:\Users\Autry\accum-flip\Prv1311\reregister_all_tasks.ps1"

Claude cannot run this (session is not elevated, confirmed 2026-08-11/12) --
this script has to be run by a human with admin rights.
#>

$RepoDir = "C:\Users\Autry\accum-flip\Prv1311"

Write-Host "=============================================================="
Write-Host "  PRV1311 -- re-registering 7 free services (no real spend)"
Write-Host "=============================================================="

$FreeScripts = @(
    "install_footprint_worker_task.ps1",
    "install_rider_team_task.ps1",
    "install_rider_flare_task.ps1",
    "install_divergence_recorder_task.ps1",
    "install_onchain_divergence_recorder_task.ps1",
    "install_solo_rider_task.ps1",
    "install_run_all_task.ps1"
)

foreach ($script in $FreeScripts) {
    Write-Host ""
    Write-Host "--- Running $script ---" -ForegroundColor Cyan
    powershell -ExecutionPolicy Bypass -File (Join-Path $RepoDir $script)
}

Write-Host ""
Write-Host "=============================================================="
Write-Host "  !!! NEXT: PRV1311-AnchorWriter -- REAL MAINNET FLR SPEND !!!" -ForegroundColor Yellow
Write-Host "=============================================================="
Write-Host "Registering this task auto-starts it within seconds and will send"
Write-Host "up to 5 real signed transactions on Flare mainnet (~0.09-0.17 FLR"
Write-Host "each), capped at MAX_FLR_PER_DAY=1.8 and MIN_WALLET_BALANCE=2.0."
Write-Host ""
$confirm = Read-Host "Type exactly REGISTER ANCHOR WRITER to proceed, anything else skips it"
if ($confirm -eq "REGISTER ANCHOR WRITER") {
    powershell -ExecutionPolicy Bypass -File (Join-Path $RepoDir "install_anchor_writer_task.ps1")
} else {
    Write-Host "Skipped -- PRV1311-AnchorWriter was NOT registered. Re-run this script, or run" -ForegroundColor Yellow
    Write-Host "install_anchor_writer_task.ps1 directly, when you're ready." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=============================================================="
Write-Host "  Done. Verify with:"
Write-Host "    Get-ScheduledTask | Where-Object {`$_.TaskName -like '*PRV1311*'} | Select TaskName, State"
Write-Host "=============================================================="
