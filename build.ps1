# Збірка Lagnix однією командою: .\build.ps1  (-SkipInstaller — лише dist\Lagnix\Lagnix.exe)
param([switch]$SkipInstaller)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$version = "1.0.0"

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

python -m PyInstaller --noconfirm --clean --onedir --windowed --uac-admin `
    --name Lagnix `
    --icon assets\lagnix.ico `
    --version-file installer\version_info.txt `
    --add-data "locales;locales" `
    --add-data "assets;assets" `
    --add-data "libs;libs" `
    --collect-all customtkinter `
    --collect-all pythonnet `
    --collect-all clr_loader `
    --hidden-import clr `
    --hidden-import pystray._win32 `
    --hidden-import pynvml `
    --collect-submodules core `
    --collect-submodules ui `
    main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller завершився з кодом $LASTEXITCODE" }
if (-not (Test-Path dist\Lagnix\Lagnix.exe)) { throw "dist\Lagnix\Lagnix.exe не створено" }
Write-Host "OK: dist\Lagnix\Lagnix.exe"

if ($SkipInstaller) { return }
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 не знайдено" }
& $iscc "/DAppVersion=$version" installer\Lagnix.iss
if ($LASTEXITCODE -ne 0) { throw "ISCC завершився з кодом $LASTEXITCODE" }
Write-Host "OK: dist\Lagnix-Setup-$version.exe"
