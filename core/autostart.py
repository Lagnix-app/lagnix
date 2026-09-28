"""Список і керування програмами автозапуску: HKCU/HKLM\\...\\Run і папки Startup.

Вимкнення нічого не стирає назавжди:
- для реєстру значення виймається з ключа Run, а сам запис (джерело, назва,
  команда) зберігається в config.json (`autostart_disabled`), звідки при
  увімкненні записується назад;
- для ярликів у папці Startup файл переноситься у приховану підпапку
  `PulseFPS_Disabled` тієї ж теки Startup і повертається назад при увімкненні;
  сам запис так само дублюється в config.json для надійного відновлення.

HKLM\\...\\Run, HKLM\\...\\WOW6432Node\\...\\Run і спільна папка Startup (усі
користувачі) потребують прав адміністратора для зміни — HKCU і особиста
Startup доступні без підвищення прав.
"""

import os
import shutil
import winreg

from core.admin import is_admin
from core.settings import load_settings, update_setting

_RUN_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_SUBKEY_WOW64 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"

SOURCE_HKCU = "HKCU"
SOURCE_HKLM = "HKLM"
SOURCE_HKLM32 = "HKLM32"
SOURCE_STARTUP_USER = "StartupUser"
SOURCE_STARTUP_COMMON = "StartupCommon"

SOURCE_LABELS = {
    SOURCE_HKCU: "Реєстр — поточний користувач",
    SOURCE_HKLM: "Реєстр — усі користувачі",
    SOURCE_HKLM32: "Реєстр — усі користувачі (32-біт)",
    SOURCE_STARTUP_USER: "Папка автозавантаження — користувач",
    SOURCE_STARTUP_COMMON: "Папка автозавантаження — усі користувачі",
}

# (джерело, hive, шлях підключа, потрібні права адміністратора)
_REGISTRY_SOURCES = (
    (SOURCE_HKCU, winreg.HKEY_CURRENT_USER, _RUN_SUBKEY, False),
    (SOURCE_HKLM, winreg.HKEY_LOCAL_MACHINE, _RUN_SUBKEY, True),
    (SOURCE_HKLM32, winreg.HKEY_LOCAL_MACHINE, _RUN_SUBKEY_WOW64, True),
)

_DISABLED_DIR_NAME = "PulseFPS_Disabled"
_IGNORED_STARTUP_NAMES = {"desktop.ini"}


def _user_startup_dir() -> str:
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")


def _common_startup_dir() -> str:
    program_data = os.environ.get("ProgramData", r"C:\ProgramData")
    return os.path.join(program_data, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")


def _entry_id(source: str, name: str) -> str:
    return f"{source}:{name}"


def _disabled_state() -> dict:
    return dict(load_settings().get("autostart_disabled", {}))


def _save_disabled_state(state: dict) -> None:
    update_setting("autostart_disabled", state)


def requires_admin_for(source: str) -> bool:
    return source in (SOURCE_HKLM, SOURCE_HKLM32, SOURCE_STARTUP_COMMON)


# ------------------------------------------------------------------ реєстр

def _read_run_values(hive, subkey_path) -> dict[str, str]:
    values = {}
    try:
        with winreg.OpenKey(hive, subkey_path, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if isinstance(value, str) and value:
                    values[name] = value
    except OSError:
        pass
    return values


def _write_run_value(hive, subkey_path, name, value) -> bool:
    try:
        with winreg.CreateKeyEx(hive, subkey_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        return True
    except OSError:
        return False


def _delete_run_value(hive, subkey_path, name) -> bool:
    try:
        with winreg.OpenKey(hive, subkey_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
        return True
    except OSError:
        return False


def _registry_source_info(source: str):
    for src, hive, subkey_path, requires_admin in _REGISTRY_SOURCES:
        if src == source:
            return hive, subkey_path, requires_admin
    return None


# ----------------------------------------------------------------- startup

def _list_startup_files(directory: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    try:
        names = os.listdir(directory)
    except OSError:
        return []

    result = []
    for name in names:
        if name in _IGNORED_STARTUP_NAMES or name == _DISABLED_DIR_NAME:
            continue
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            result.append(name)
    return result


def _startup_dir_for(source: str) -> str:
    return _user_startup_dir() if source == SOURCE_STARTUP_USER else _common_startup_dir()


# -------------------------------------------------------------------- list

def list_entries() -> list[dict]:
    """Усі програми автозапуску: активні (реєстр/папки) + вимкнені (config.json)."""
    disabled = _disabled_state()
    entries = []
    seen_ids = set()

    for source, hive, subkey_path, requires_admin in _REGISTRY_SOURCES:
        for name, command in _read_run_values(hive, subkey_path).items():
            entry_id = _entry_id(source, name)
            seen_ids.add(entry_id)
            entries.append({
                "id": entry_id, "source": source, "name": name, "command": command,
                "enabled": True, "requires_admin": requires_admin,
            })

    for source in (SOURCE_STARTUP_USER, SOURCE_STARTUP_COMMON):
        directory = _startup_dir_for(source)
        for name in _list_startup_files(directory):
            entry_id = _entry_id(source, name)
            seen_ids.add(entry_id)
            entries.append({
                "id": entry_id, "source": source, "name": name,
                "command": os.path.join(directory, name),
                "enabled": True, "requires_admin": requires_admin_for(source),
            })

    for entry_id, record in disabled.items():
        if entry_id in seen_ids:
            continue
        source = record["source"]
        entries.append({
            "id": entry_id, "source": source, "name": record["name"],
            "command": record["command"], "enabled": False,
            "requires_admin": requires_admin_for(source),
        })

    entries.sort(key=lambda e: (SOURCE_LABELS.get(e["source"], e["source"]), e["name"].lower()))
    return entries


# ------------------------------------------------------------------ toggle

def disable_entry(entry: dict) -> tuple[bool, str]:
    """Вимикає активний запис автозапуску, зберігаючи його в config.json для відновлення."""
    source = entry["source"]

    if source in (SOURCE_HKCU, SOURCE_HKLM, SOURCE_HKLM32):
        hive, subkey_path, requires_admin = _registry_source_info(source)
        if requires_admin and not is_admin():
            return False, "Потрібні права адміністратора"
        if not _delete_run_value(hive, subkey_path, entry["name"]):
            return False, "Не вдалося видалити запис із реєстру"

    elif source in (SOURCE_STARTUP_USER, SOURCE_STARTUP_COMMON):
        if requires_admin_for(source) and not is_admin():
            return False, "Потрібні права адміністратора"
        directory = _startup_dir_for(source)
        disabled_dir = os.path.join(directory, _DISABLED_DIR_NAME)
        try:
            os.makedirs(disabled_dir, exist_ok=True)
            shutil.move(entry["command"], os.path.join(disabled_dir, entry["name"]))
        except OSError as exc:
            return False, str(exc)
    else:
        return False, "Невідоме джерело автозапуску"

    disabled = _disabled_state()
    disabled[entry["id"]] = {"source": source, "name": entry["name"], "command": entry["command"]}
    _save_disabled_state(disabled)
    return True, ""


def enable_entry(entry_id: str) -> tuple[bool, str]:
    """Повертає раніше вимкнений запис назад у реєстр/папку Startup за даними з config.json."""
    disabled = _disabled_state()
    record = disabled.get(entry_id)
    if record is None:
        return False, "Запис не знайдено серед вимкнених"

    source = record["source"]

    if source in (SOURCE_HKCU, SOURCE_HKLM, SOURCE_HKLM32):
        hive, subkey_path, requires_admin = _registry_source_info(source)
        if requires_admin and not is_admin():
            return False, "Потрібні права адміністратора"
        if not _write_run_value(hive, subkey_path, record["name"], record["command"]):
            return False, "Не вдалося відновити запис у реєстрі"

    elif source in (SOURCE_STARTUP_USER, SOURCE_STARTUP_COMMON):
        if requires_admin_for(source) and not is_admin():
            return False, "Потрібні права адміністратора"
        directory = _startup_dir_for(source)
        disabled_path = os.path.join(directory, _DISABLED_DIR_NAME, record["name"])
        try:
            shutil.move(disabled_path, record["command"])
        except OSError as exc:
            return False, str(exc)
    else:
        return False, "Невідоме джерело автозапуску"

    del disabled[entry_id]
    _save_disabled_state(disabled)
    return True, ""
