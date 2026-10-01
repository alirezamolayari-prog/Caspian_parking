# Builds the Windows installer (SPEC §7):
#   1. quality gate (skip with -SkipCheck)
#   2. icon + version resource (packaging\make_assets.py)
#   3. PyInstaller onedir build -> dist\parking\
#   4. smoke test of the built exe
#   5. Inno Setup installer -> dist\installer\<AppFolder>-Setup-<version>.exe (+ version.json for the update share)
param(
    [switch]$SkipCheck,
    [string]$Iscc = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'

if (-not $SkipCheck) {
    & (Join-Path $PSScriptRoot 'check.ps1')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host '==> assets'
& $python packaging\make_assets.py build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$vars = @{}
Get-Content build\build_vars.txt -Encoding UTF8 | ForEach-Object {
    $pair = $_ -split '=', 2
    if ($pair.Count -eq 2) { $vars[$pair[0]] = $pair[1] }
}

Write-Host '==> PyInstaller'
& (Join-Path $root '.venv\Scripts\pyinstaller.exe') --noconfirm --clean --distpath dist --workpath build\pyi packaging\parking.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host '==> smoke test of the built app'
$env:QT_QPA_PLATFORM = 'offscreen'
$smoke = Start-Process -FilePath (Join-Path $root 'dist\parking\parking.exe') -ArgumentList '--smoke' -Wait -PassThru
Remove-Item Env:\QT_QPA_PLATFORM
if ($smoke.ExitCode -ne 0) {
    Write-Host "built app failed the smoke test (exit $($smoke.ExitCode))" -ForegroundColor Red
    exit 1
}

Write-Host '==> installer'
if (-not $Iscc) {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    )
    $uninstall = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1' -ErrorAction SilentlyContinue
    foreach ($entry in $uninstall) {
        if ($entry.InstallLocation) { $candidates += (Join-Path $entry.InstallLocation 'ISCC.exe') }
    }
    $Iscc = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}
if (-not $Iscc) {
    Write-Host 'Inno Setup 6 not found: the app is built in dist\parking, the installer is skipped.' -ForegroundColor Yellow
    exit 0
}
& $Iscc /Q "/DAppName=$($vars['AppName'])" "/DAppFolder=$($vars['AppFolder'])" "/DAppVersion=$($vars['AppVersion'])" "/DRoot=$root" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$installer = "$($vars['AppFolder'])-Setup-$($vars['AppVersion']).exe"

Write-Host '==> install test (per user, silent): install, smoke test the installed app, uninstall'
$testDir = Join-Path $env:TEMP "parking-install-test-$PID"
$setup = Start-Process -FilePath (Join-Path $root "dist\installer\$installer") -Wait -PassThru -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', '/NOLAUNCH=1', '/TASKS=""', "/DIR=`"$testDir`"")
if ($setup.ExitCode -ne 0) { Write-Host "installer failed (exit $($setup.ExitCode))" -ForegroundColor Red; exit 1 }
$env:QT_QPA_PLATFORM = 'offscreen'
$installed = Start-Process -FilePath (Join-Path $testDir 'parking.exe') -ArgumentList '--smoke' -Wait -PassThru
Remove-Item Env:\QT_QPA_PLATFORM
$uninstall = Start-Process -FilePath (Join-Path $testDir 'unins000.exe') -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES' -Wait -PassThru
if ($installed.ExitCode -ne 0) { Write-Host 'installed app failed the smoke test' -ForegroundColor Red; exit 1 }
if ($uninstall.ExitCode -ne 0) { Write-Host 'uninstaller failed' -ForegroundColor Red; exit 1 }
Start-Sleep -Seconds 2
if (Test-Path (Join-Path $testDir 'parking.exe')) { Write-Host 'uninstall left program files behind' -ForegroundColor Red; exit 1 }
Write-Host 'install test passed' -ForegroundColor Green
$manifest = @{ version = $vars['AppVersion']; installer = $installer; notes = '' } | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $root 'dist\installer\version.json'), $manifest, (New-Object System.Text.UTF8Encoding $false))
Write-Host "installer: dist\installer\$installer" -ForegroundColor Green
Write-Host 'copy dist\installer\* to the update share on the server to update the gates'
exit 0
