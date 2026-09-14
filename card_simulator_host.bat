@echo off
rem card_simulator_host - one-key launcher for the EMV auto-test host.
rem Usage:
rem   card_simulator_host                 start with defaults (web :8000, device TCP :9100)
rem   card_simulator_host --port 8123     override the web port
rem   card_simulator_host --help          show all server options
rem   card_simulator_host status|list|run|stop|abort|skip   control subcommands
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"

set "PYTHONPATH=%ROOT%backend"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
    echo [card_simulator_host] Python 3.12+ was not found on PATH.
    echo Install Python first, or run:  python -m emv_host serve
    exit /b 1
)

%PY% -m emv_host %*
exit /b %errorlevel%
