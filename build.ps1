# Збірка Lagnix однією командою: .\build.ps1  (-SkipInstaller — лише dist\Lagnix\Lagnix.exe)
param([switch]$SkipInstaller)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$version = "1.0.0"

# Підпис коду (необов'язковий): задайте LAGNIX_SIGN_CERT (шлях до .pfx) і LAGNIX_SIGN_PASSWORD
# або LAGNIX_SIGN_THUMBPRINT (сертифікат у сховищі). LAGNIX_SIGN_TIMESTAMP — сервер часових міток.
function Invoke-Sign([string]$file) {
    if (-not ($env:LAGNIX_SIGN_CERT -or $env:LAGNIX_SIGN_THUMBPRINT)) { return }
    $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match "\\x64\\" } | Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $signtool) { throw "signtool.exe не знайдено (Windows SDK)" }
    $ts = if ($env:LAGNIX_SIGN_TIMESTAMP) { $env:LAGNIX_SIGN_TIMESTAMP } else { "http://timestamp.digicert.com" }
    $args = @("sign", "/fd", "SHA256", "/tr", $ts, "/td", "SHA256")
    if ($env:LAGNIX_SIGN_CERT) { $args += @("/f", $env:LAGNIX_SIGN_CERT, "/p", $env:LAGNIX_SIGN_PASSWORD) }
    else { $args += @("/sha1", $env:LAGNIX_SIGN_THUMBPRINT) }
    & $signtool.FullName @args $file
    if ($LASTEXITCODE -ne 0) { throw "signtool завершився з кодом $LASTEXITCODE для $file" }
    Write-Host "Підписано: $file"
}

# Офіційний інсталятор драйвера PawnIO: у git не зберігається, завантажується й перевіряється.
function Get-PawnIO {
    $path = "installer\redist\PawnIO_setup.exe"
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Force installer\redist | Out-Null
        Invoke-WebRequest "https://github.com/namazso/PawnIO.Setup/releases/latest/download/PawnIO_setup.exe" -OutFile $path -UseBasicParsing
    }
    $sig = Get-AuthenticodeSignature $path
    if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "namazso") {
        Remove-Item $path -Force
        throw "Підпис PawnIO_setup.exe недійсний або не від namazso: $($sig.Status)"
    }
    Write-Host "PawnIO_setup.exe: підпис дійсний ($($sig.SignerCertificate.Subject))"
}

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
Invoke-Sign "dist\Lagnix\Lagnix.exe"
Write-Host "OK: dist\Lagnix\Lagnix.exe"

if ($SkipInstaller) { return }
Get-PawnIO
# Картинки майстра (робот у настроях) генеруються з ui/widgets/robot.py: у git не зберігаються
python tools\gen_installer_images.py
if ($LASTEXITCODE -ne 0) { throw "gen_installer_images.py завершився з кодом $LASTEXITCODE" }
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 не знайдено" }
& $iscc "/DAppVersion=$version" installer\Lagnix.iss
if ($LASTEXITCODE -ne 0) { throw "ISCC завершився з кодом $LASTEXITCODE" }
Invoke-Sign "dist\Lagnix-Setup-$version.exe"
Write-Host "OK: dist\Lagnix-Setup-$version.exe"
