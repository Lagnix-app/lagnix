"""Драйвер датчиків PawnIO: чи встановлено, і встановлення за згодою користувача.

LibreHardwareMonitorLib 0.9.6 читає температуру CPU через підписаний драйвер
PawnIO (github.com/namazso/PawnIO, pawnio.eu). PulseFPS не вбудовує драйвер:
після явної згоди користувача install() завантажує ОФІЦІЙНИЙ інсталятор
(посилання з pawnio.eu), перевіряє його підпис Authenticode і лише тоді
запускає звичайний майстер встановлення (його прапорців тихого режиму автор
не документує — тож не вгадуємо, а показуємо майстер). Усе — в logs.txt.
"""

import json
import os
import subprocess
import tempfile
import urllib.request
import winreg

from core.logging_setup import get_audit_logger, get_logger
from core.i18n import t

INSTALLER_URL = "https://github.com/namazso/PawnIO.Setup/releases/latest/download/PawnIO_setup.exe"
_MAX_SIZE = 50 * 1024 * 1024
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_log = get_logger("core.pawnio")

_UNINSTALL_ROOTS = (
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY),
)


def _service_installed() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\PawnIO"):
            return True
    except OSError:
        return False


def _uninstall_entry() -> str | None:
    for hive, path, view in _UNINSTALL_ROOTS:
        try:
            root = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            index = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(root, sub) as key:
                        name = str(winreg.QueryValueEx(key, "DisplayName")[0])
                except OSError:
                    continue
                if "pawnio" in name.lower():
                    try:
                        with winreg.OpenKey(root, sub) as key:
                            version = str(winreg.QueryValueEx(key, "DisplayVersion")[0])
                    except OSError:
                        version = ""
                    return f"{name} {version}".strip()
    return None


def status() -> dict:
    """{"installed": bool, "name": str | None} — лише читання реєстру."""
    entry = _uninstall_entry()
    return {"installed": bool(entry) or _service_installed(), "name": entry}


def _verify_signature(path: str) -> tuple[bool, str]:
    """Authenticode через PowerShell Get-AuthenticodeSignature: (дійсний?, підписант)."""
    script = (
        "$s = Get-AuthenticodeSignature -LiteralPath $env:PULSEFPS_CHECK; "
        "@{status = [string]$s.Status; signer = [string]$s.SignerCertificate.Subject} | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=60, creationflags=_NO_WINDOW,
            env=dict(os.environ, PULSEFPS_CHECK=path),
        )
        data = json.loads(result.stdout.decode("utf-8", errors="replace").strip() or "{}")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return False, t("pawnio.err.verify", exc=exc)
    return data.get("status") == "Valid", data.get("signer") or ""


def download_and_verify(reason: str) -> str:
    """Завантажує офіційний інсталятор і перевіряє підпис. -> шлях. Кидає RuntimeError з поясненням."""
    audit = get_audit_logger()
    folder = os.path.join(tempfile.gettempdir(), "PulseFPS")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "PawnIO_setup.exe")
    audit.info("Downloading the PawnIO installer from %s — reason: %s", INSTALLER_URL, reason)
    request = urllib.request.Request(INSTALLER_URL, headers={"User-Agent": "PulseFPS"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(path, "wb") as out:
            size = 0
            while chunk := response.read(64 * 1024):
                size += len(chunk)
                if size > _MAX_SIZE:
                    raise RuntimeError(t("pawnio.err.too_big"))
                out.write(chunk)
    except OSError as exc:
        raise RuntimeError(t("pawnio.err.download", exc=exc)) from exc

    valid, signer = _verify_signature(path)
    if not valid:
        _log.error("The PawnIO installer failed signature verification: %s", signer)
        try:
            os.remove(path)
        except OSError:
            pass
        raise RuntimeError(t("pawnio.err.bad_signature", signer=signer or t("pawnio.no_signature")))
    audit.info("The PawnIO installer signature is valid: %s", signer)
    return path


def run_installer(path: str, reason: str) -> int:
    """Запускає майстер встановлення (PulseFPS уже з правами адміністратора) і чекає завершення."""
    get_audit_logger().info("Running the PawnIO setup wizard (%s) — reason: %s", path, reason)
    code = subprocess.call([path])
    get_audit_logger().info("The PawnIO wizard exited with code %s; driver: %s", code,
                            "installed" if status()["installed"] else "not installed")
    return code
