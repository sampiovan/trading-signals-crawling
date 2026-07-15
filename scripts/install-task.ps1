<#
.SYNOPSIS
Registra il crawler come Scheduled Task di Windows: parte al logon e
viene riavviato automaticamente se il processo termina con errore.

.NOTES
Eseguire da una PowerShell nella root del progetto (o passare -ProjectRoot).
Richiede il venv gia' creato (.venv) e config.ini presente in crawler\.
#>
param(
    [string]$TaskName = "TradingSignalsCrawler",
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$ConfigPath = (Join-Path (Split-Path -Parent $PSScriptRoot) "config.ini")
)

$ErrorActionPreference = "Stop"

$pythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    throw "Python del venv non trovato: $pythonExe. Crea prima il venv (python -m venv .venv; pip install -e .)."
}
if (-not (Test-Path $ConfigPath)) {
    throw "config.ini non trovato: $ConfigPath. Copia config.example.ini e compilalo."
}

# Il crawler risolve stato/sessione/log accanto al config: la workdir non è determinante
$action = New-ScheduledTaskAction -Execute $pythonExe -Argument "-m crawler --config `"$ConfigPath`"" -WorkingDirectory $ProjectRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# RestartCount/RestartInterval: riavvia il task se il processo esce con errore
# (main.py esce con codice 1 sugli errori fatali). ExecutionTimeLimit 0 = nessun
# limite di durata (il crawler resta in ascolto indefinitamente).
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "Task '$TaskName' registrata (avvio al logon di $env:USERNAME, riavvio automatico su errore)."
Write-Host "Avvio immediato..."
Start-ScheduledTask -TaskName $TaskName
Write-Host "Fatto. Log del crawler in: $(Split-Path -Parent $ConfigPath)\logs\crawler.log"
