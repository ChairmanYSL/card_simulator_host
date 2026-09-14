# card_simulator_host - PowerShell launcher for the EMV auto-test host.
# Usage (from the repository root):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\card_simulator_host.ps1
#   .\card_simulator_host.ps1 --port 8123 --device-port 9123
#   .\card_simulator_host.ps1 status
# Unlike the cmd batch launcher, Ctrl+C here does not trigger cmd's
# "Terminate batch job (Y/N)?" prompt.

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root "backend"

# Inject backend onto PYTHONPATH (keep any pre-existing entries).
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = $Backend + [System.IO.Path]::PathSeparator + $env:PYTHONPATH
} else {
    $env:PYTHONPATH = $Backend
}
Set-Location $Root

$code = 1
if (Get-Command python -ErrorAction SilentlyContinue) {
    python -m emv_host @args
    $code = $LASTEXITCODE
}
elseif (Get-Command py -ErrorAction SilentlyContinue) {
    py -3 -m emv_host @args
    $code = $LASTEXITCODE
}
else {
    Write-Host "[card_simulator_host] Python 3.12+ was not found on PATH." -ForegroundColor Red
    Write-Host "Install Python first, or run:  python -m emv_host serve"
}
exit $code
