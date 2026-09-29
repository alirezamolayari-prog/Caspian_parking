# Quality gate: ruff format + ruff + mypy (lenient) + pytest (offscreen) + smoke launch.
# Run `ruff format src tests scripts` first if the format step fails.
# Exits non-zero on the first failing step.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\check.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'Missing .venv - run scripts\setup.ps1 first' }

$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONIOENCODING = 'utf-8'

function Invoke-Step([string]$name, [scriptblock]$block) {
    Write-Host "==> $name" -ForegroundColor Cyan
    & $block
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $name" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Invoke-Step 'ruff format' { & $py -m ruff format --check src tests scripts }
Invoke-Step 'ruff' { & $py -m ruff check src tests scripts }
Invoke-Step 'mypy' { & $py -m mypy }
Invoke-Step 'pytest' { & $py -m pytest -q }
Invoke-Step 'smoke launch' { & $py -m caspian_parking --smoke }

Write-Host 'Quality gate: GREEN' -ForegroundColor Green
exit 0
