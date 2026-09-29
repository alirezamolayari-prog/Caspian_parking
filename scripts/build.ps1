# Builds the Windows installer (PyInstaller onedir + Inno Setup).
# Implemented in Phase 11; until then it only runs the quality gate.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
& (Join-Path $PSScriptRoot 'check.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'Packaging is implemented in Phase 11.' -ForegroundColor Yellow
exit 0
