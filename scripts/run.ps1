# Starts the application from source.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\run.ps1 [app arguments]
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'Missing .venv - run scripts\setup.ps1 first' }
& $py -m caspian_parking @args
exit $LASTEXITCODE
