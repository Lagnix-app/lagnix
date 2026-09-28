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

import ctypes
import os
import re
import shutil
import struct
import subprocess
import winreg
from ctypes import wintypes

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

# Технічні ідентифікатори реєстру (напр. "MicrosoftEdgeAutoLaunch_a1b2c3d4e5")
# замінюємо на FileDescription/ProductName з exe, якщо назва схожа на один із цих шаблонів.
_TECHNICAL_NAME_RE = re.compile(
    r"_[0-9a-fA-F]{6,}$|^\{[0-9a-fA-F-]{36}\}$|^[a-z0-9]+(\.[a-z0-9]+){2,}$",
    re.IGNORECASE,
)

_HIGH_IMPACT_KEYWORDS = ("steam", "teams", "vanguard", "easyanticheat", "battleye", "faceit")
_MEDIUM_IMPACT_KEYWORDS = ("discord", "onedrive", "edge", "skype", "spotify", "slack", "zoom", "dropbox")
_IMPACT_HIGH, _IMPACT_MEDIUM, _IMPACT_LOW = "висока", "середня", "низька"

_PROTECTED_NAME_KEYWORDS = ("securityhealth", "windowsdefender", "defender")

_ANTICHEAT_KEYWORDS = ("vanguard", "easyanticheat", "battleye", "faceit")
ANTICHEAT_WARNING = "Якщо вимкнути, ігри з цим античитом не запустяться до перезавантаження."

_version_field_cache: dict[tuple[str, str], str | None] = {}


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


# ------------------------------------------------------------ файл ярлика .lnk

def _resolve_lnk_target(lnk_path: str) -> str | None:
    """Дістає локальний шлях цілі з бінарного .lnk (формат MS-SHLLINK), без pywin32.

    Читає лише LinkTargetIDList (пропускається) і LinkInfo.LocalBasePath —
    цього достатньо для звичайних ярликів на локальний exe. Будь-яка
    несподіванка у форматі просто повертає None (ярлик показуємо без метаданих).
    """
    try:
        with open(lnk_path, "rb") as f:
            data = f.read()
    except OSError:
        return None

    try:
        if len(data) < 76 or data[0:4] != b"\x4c\x00\x00\x00":
            return None

        link_flags = struct.unpack_from("<I", data, 20)[0]
        offset = 76

        if link_flags & 0x1:  # HasLinkTargetIDList
            if offset + 2 > len(data):
                return None
            id_list_size = struct.unpack_from("<H", data, offset)[0]
            offset += 2 + id_list_size

        if not (link_flags & 0x2):  # HasLinkInfo
            return None

        link_info_start = offset
        if link_info_start + 24 > len(data):
            return None
        link_info_size = struct.unpack_from("<I", data, link_info_start)[0]
        if link_info_size < 24 or link_info_start + link_info_size > len(data):
            return None

        link_info_flags = struct.unpack_from("<I", data, link_info_start + 8)[0]
        local_base_path_offset = struct.unpack_from("<I", data, link_info_start + 16)[0]
        common_suffix_offset = struct.unpack_from("<I", data, link_info_start + 20)[0]

        if not (link_info_flags & 0x1) or local_base_path_offset == 0:
            return None

        def _read_cstr(start: int) -> str:
            end = data.find(b"\x00", start)
            if end == -1:
                end = len(data)
            return data[start:end].decode("mbcs", errors="ignore")

        base = _read_cstr(link_info_start + local_base_path_offset)
        suffix = _read_cstr(link_info_start + common_suffix_offset) if common_suffix_offset else ""
        target = base + suffix
        return target or None
    except (struct.error, IndexError, UnicodeDecodeError):
        return None


# -------------------------------------------------------- метадані exe (version.dll)

_version_dll = ctypes.WinDLL("version")
_version_dll.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
_version_dll.GetFileVersionInfoSizeW.restype = wintypes.DWORD
_version_dll.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
_version_dll.GetFileVersionInfoW.restype = wintypes.BOOL
_version_dll.VerQueryValueW.argtypes = [
    wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)
]
_version_dll.VerQueryValueW.restype = wintypes.BOOL


def _read_version_field(exe_path: str, field: str) -> str | None:
    cache_key = (exe_path, field)
    if cache_key in _version_field_cache:
        return _version_field_cache[cache_key]

    value = _read_version_field_uncached(exe_path, field)
    _version_field_cache[cache_key] = value
    return value


def _read_version_field_uncached(exe_path: str, field: str) -> str | None:
    try:
        size = _version_dll.GetFileVersionInfoSizeW(exe_path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not _version_dll.GetFileVersionInfoW(exe_path, 0, size, buf):
            return None

        trans_ptr = ctypes.c_void_p()
        trans_len = wintypes.UINT()
        langs = [(1033, 1200)]  # запасний варіант: en-US, Unicode
        if _version_dll.VerQueryValueW(
            buf, r"\VarFileInfo\Translation", ctypes.byref(trans_ptr), ctypes.byref(trans_len)
        ) and trans_len.value >= 4:
            count = trans_len.value // 4
            arr = ctypes.cast(trans_ptr, ctypes.POINTER(ctypes.c_uint32 * count)).contents
            langs = [(arr[i] & 0xFFFF, (arr[i] >> 16) & 0xFFFF) for i in range(count)]

        for lang, codepage in langs:
            sub_block = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\{field}"
            value_ptr = ctypes.c_void_p()
            value_len = wintypes.UINT()
            if (
                _version_dll.VerQueryValueW(buf, sub_block, ctypes.byref(value_ptr), ctypes.byref(value_len))
                and value_len.value
            ):
                text = ctypes.wstring_at(value_ptr, value_len.value - 1).strip()
                if text:
                    return text
        return None
    except OSError:
        return None


# --------------------------------------------------------- класифікація записів

def _looks_technical(name: str) -> bool:
    return bool(_TECHNICAL_NAME_RE.search(name))


def _metadata_source_path(resolved_path: str | None) -> str | None:
    if not resolved_path:
        return None
    lower = resolved_path.lower()
    if lower.endswith(".lnk"):
        target = _resolve_lnk_target(resolved_path)
        return target if target and os.path.isfile(target) else None
    if lower.endswith(".exe") and os.path.isfile(resolved_path):
        return resolved_path
    return None


def _known_impact(name: str, description: str | None, publisher: str | None) -> str | None:
    haystack = " ".join(filter(None, (name, description, publisher))).lower()
    if any(keyword in haystack for keyword in _HIGH_IMPACT_KEYWORDS):
        return _IMPACT_HIGH
    if any(keyword in haystack for keyword in _MEDIUM_IMPACT_KEYWORDS):
        return _IMPACT_MEDIUM
    return None


def _impact_by_size(path: str | None) -> str:
    if not path:
        return _IMPACT_LOW
    try:
        size = os.path.getsize(path)
    except OSError:
        return _IMPACT_LOW
    if size >= 50 * 1024 * 1024:
        return _IMPACT_HIGH
    if size >= 5 * 1024 * 1024:
        return _IMPACT_MEDIUM
    return _IMPACT_LOW


def _is_protected_system(name: str, path: str | None) -> bool:
    if any(keyword in name.lower() for keyword in _PROTECTED_NAME_KEYWORDS):
        return True
    if not path:
        return False
    system_root = os.path.abspath(os.environ.get("SystemRoot", r"C:\Windows"))
    try:
        return os.path.commonpath([os.path.abspath(path), system_root]) == system_root
    except ValueError:
        return False


def _is_anticheat(name: str, description: str | None, publisher: str | None, path: str | None) -> bool:
    haystack = " ".join(filter(None, (name, description, publisher, path))).lower()
    return any(keyword in haystack for keyword in _ANTICHEAT_KEYWORDS)


def _extract_exe_path(command: str) -> str | None:
    """Дістає шлях виконуваного файлу з рядка команди Run (може містити аргументи)."""
    stripped = command.strip()
    if not stripped:
        return None
    if stripped.startswith('"'):
        end = stripped.find('"', 1)
        return stripped[1:end] if end != -1 else stripped[1:]
    lower = stripped.lower()
    exe_pos = lower.find(".exe")
    if exe_pos != -1:
        return stripped[: exe_pos + 4]
    return stripped.split(" ")[0]


def _entry_current_path(entry: dict) -> str | None:
    """Реальний шлях до файлу запису зараз (враховуючи, що вимкнені ярлики
    Startup фізично лежать у прихованій підпапці, а не за оригінальним шляхом).
    """
    source = entry["source"]
    if source in (SOURCE_STARTUP_USER, SOURCE_STARTUP_COMMON):
        directory = _startup_dir_for(source)
        path = (
            os.path.join(directory, entry["name"])
            if entry["enabled"]
            else os.path.join(directory, _DISABLED_DIR_NAME, entry["name"])
        )
        return path if os.path.exists(path) else None

    exe_path = _extract_exe_path(entry["command"])
    return exe_path if exe_path and os.path.isfile(exe_path) else None


def _enrich_entry(entry: dict) -> None:
    resolved_path = _entry_current_path(entry)
    entry["resolved_path"] = resolved_path

    meta_path = _metadata_source_path(resolved_path)
    description = _read_version_field(meta_path, "FileDescription") if meta_path else None
    product_name = _read_version_field(meta_path, "ProductName") if meta_path else None
    publisher = _read_version_field(meta_path, "CompanyName") if meta_path else None

    friendly = description or product_name
    entry["display_name"] = friendly if (friendly and _looks_technical(entry["name"])) else entry["name"]
    entry["publisher"] = publisher

    size_path = meta_path or resolved_path
    entry["is_system"] = _is_protected_system(entry["name"], size_path)
    entry["is_anticheat"] = _is_anticheat(entry["name"], description, publisher, size_path)
    entry["impact"] = _known_impact(entry["name"], description, publisher) or _impact_by_size(size_path)


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

    for entry in entries:
        _enrich_entry(entry)

    entries.sort(key=lambda e: (SOURCE_LABELS.get(e["source"], e["source"]), e["name"].lower()))
    return entries


def open_location(entry: dict) -> tuple[bool, str]:
    """Відкриває Провідник із виділеним файлом запису (exe/ярлик)."""
    path = entry.get("resolved_path")
    if not path or not os.path.exists(path):
        return False, "Не вдалося визначити розташування файлу"
    try:
        subprocess.Popen(["explorer", "/select,", path])
        return True, ""
    except OSError as exc:
        return False, str(exc)


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
