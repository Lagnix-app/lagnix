"""Список встановлених програм із реєстру Windows (Uninstall) та їх видалення.

Видалення завжди виконується через офіційний UninstallString самої програми
(записаний її інсталятором) — файли програми ніколи не видаляються вручну.
"""

import os
import subprocess
import winreg
from datetime import datetime

_UNINSTALL_ROOTS = (
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM32"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKCU"),
)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _read_value(key, name, default=None):
    try:
        value, _ = winreg.QueryValueEx(key, name)
        return value
    except OSError:
        return default


def _parse_install_date(raw):
    """InstallDate у реєстрі — рядок YYYYMMDD, але буває пошкодженим (напр. Discord
    пише невалідні дні/місяці). datetime.strptime сам відхилить таке — повертаємо None.
    """
    if not raw or not isinstance(raw, str) or len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return None


def _guess_install_folder(entry_key, uninstall_string: str) -> str | None:
    location = _read_value(entry_key, "InstallLocation")
    if location and os.path.isdir(location):
        return location

    stripped = uninstall_string.strip()
    if stripped.startswith('"'):
        end = stripped.find('"', 1)
        exe_path = stripped[1:end] if end != -1 else stripped[1:]
    else:
        exe_path = stripped.split(" ")[0]

    folder = os.path.dirname(exe_path)
    return folder if folder and os.path.isdir(folder) else None


def list_installed_programs() -> list[dict]:
    """Читає гілки Uninstall (HKLM, HKLM WOW6432Node, HKCU) і повертає видимі програми.

    Пропускає компоненти системи (SystemComponent=1), оновлення без власного
    запису (ParentKeyName) і записи без UninstallString — їх нічим видаляти.
    """
    programs = []

    for hive, subkey_path, hive_name in _UNINSTALL_ROOTS:
        try:
            root_key = winreg.OpenKey(hive, subkey_path)
        except OSError:
            continue

        with root_key:
            index = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(root_key, index)
                except OSError:
                    break
                index += 1

                try:
                    with winreg.OpenKey(root_key, subkey_name) as entry_key:
                        name = _read_value(entry_key, "DisplayName")
                        uninstall_string = _read_value(entry_key, "UninstallString")
                        if not name or not uninstall_string:
                            continue
                        if _read_value(entry_key, "SystemComponent", 0) == 1:
                            continue
                        if _read_value(entry_key, "ParentKeyName"):
                            continue

                        size_kb = _read_value(entry_key, "EstimatedSize", 0)
                        try:
                            size_bytes = int(size_kb) * 1024
                        except (TypeError, ValueError):
                            size_bytes = 0

                        programs.append({
                            "key": f"{hive_name}\\{subkey_name}",
                            "name": str(name),
                            "publisher": str(_read_value(entry_key, "Publisher", "") or ""),
                            "version": str(_read_value(entry_key, "DisplayVersion", "") or ""),
                            "size_bytes": size_bytes,
                            "install_date": _parse_install_date(_read_value(entry_key, "InstallDate")),
                            "uninstall_string": str(uninstall_string),
                            "install_folder": _guess_install_folder(entry_key, str(uninstall_string)),
                        })
                except OSError:
                    continue

    programs.sort(key=lambda p: p["name"].lower())
    return programs


def uninstall_program(uninstall_string: str) -> tuple[bool, str]:
    """Запускає офіційний UninstallString програми як є, без модифікацій."""
    if not uninstall_string:
        return False, "Немає команди видалення"

    try:
        subprocess.Popen(uninstall_string, shell=True, creationflags=_NO_WINDOW)
        return True, ""
    except OSError as exc:
        return False, str(exc)
