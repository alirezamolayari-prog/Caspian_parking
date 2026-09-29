# Performance benchmarks (SPEC 2.4) on the 1,000,000-visit synthetic dataset.
# Seeds .bigdata\data once (about 2-3 minutes), then runs the perf tests and writes tests\artifacts\perf.json.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONIOENCODING = 'utf-8'
& $py scripts\seed_bigdata.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $py -m pytest -q -m perf tests\perf -p no:cacheprovider
$code = $LASTEXITCODE
Get-Content tests\artifacts\perf.json
exit $code
