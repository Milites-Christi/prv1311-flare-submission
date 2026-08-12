<#
Removes the PRV1311-AnchorWriter scheduled task. Does not stop/kill any
already-running python.exe process started by it -- use taskkill for that.
Does not touch the anchor_log table or the deployed mainnet contract --
uninstalling the task only stops future scheduled cycles.

MUST be run from an elevated ("Run as Administrator") PowerShell:

    powershell -ExecutionPolicy Bypass -File "C:\Users\Autry\accum-flip\Prv1311\uninstall_anchor_writer_task.ps1"
#>

$TaskName = "PRV1311-AnchorWriter"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Task '$TaskName' removed."
} else {
    Write-Host "No task named '$TaskName' found."
}
