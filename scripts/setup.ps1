# Creates .venv with Python 3.12 x64 and installs all dependencies.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
    uv python install 3.12
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        uv venv .venv --python cpython-3.12-windows-x86_64-none
    }
    uv pip install --python .venv -r requirements-dev.txt
    uv pip install --python .venv -e . --no-deps
} else {
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        py -3.12 -m venv .venv
    }
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
    & .\.venv\Scripts\python.exe -m pip install -e . --no-deps
}

& .\.venv\Scripts\python.exe -c "import sys, platform; assert sys.version_info[:2] == (3, 12), sys.version; assert platform.architecture()[0] == '64bit'; print('Python OK:', sys.version)"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 x64 is required' }
Write-Host 'Setup complete.' -ForegroundColor Green
