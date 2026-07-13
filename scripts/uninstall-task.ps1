<#
.SYNOPSIS
Ferma e rimuove la Scheduled Task del crawler creata da install-task.ps1.
#>
param(
    [string]$TaskName = "TradingSignalsCrawler"
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "Task '$TaskName' non trovata: niente da rimuovere."
    exit 0
}

if ($task.State -eq "Running") {
    Stop-ScheduledTask -TaskName $TaskName
    Write-Host "Task '$TaskName' fermata."
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Task '$TaskName' rimossa."
