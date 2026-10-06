"""Список і керування програмами автозапуску: HKCU/HKLM\\...\\Run і папки Startup.

Вимкнення — стандартний механізм Windows (як у Диспетчері завдань), нічого не
переноситься й не видаляється: у ключі
`...\\Explorer\\StartupApproved\\{Run | Run32 | StartupFolder}` (HKCU або HKLM)
ставиться бінарне значення на 12 байт: перший байт 02 — увімкнено, 03 —
вимкнено, далі час зміни (FILETIME). Ярлики Startup і значення Run лишаються
на місці. Перед першою зміною ключ зберігається в .reg (як для твіків).

Старі версії (до 0.9.4) переносили ярлики у `Startup\\Lagnix_Disabled` (Windows
відкривала цю теку в Провіднику при кожному вході) і виймали значення з Run
у data.json; `migrate_legacy()` повертає їх на місце й позначає вимкненими.

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
import time
import winreg
from ctypes import wintypes

from core.admin import is_admin
from core.logging_setup import get_audit_logger, get_logger
from core.app_data import load_data, update_data
from core.i18n import TDict, t

_RUN_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_SUBKEY_WOW64 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"

SOURCE_HKCU = "HKCU"
SOURCE_HKLM = "HKLM"
SOURCE_HKLM32 = "HKLM32"
SOURCE_STARTUP_USER = "StartupUser"
SOURCE_STARTUP_COMMON = "StartupCommon"

SOURCE_LABELS = TDict({
    SOURCE_HKCU: "autostart.source.hkcu",
    SOURCE_HKLM: "autostart.source.hklm",
    SOURCE_HKLM32: "autostart.source.hklm32",
    SOURCE_STARTUP_USER: "autostart.source.startup_user",
    SOURCE_STARTUP_COMMON: "autostart.source.startup_common",
})

# (джерело, hive, шлях підключа, потрібні права адміністратора)
_REGISTRY_SOURCES = (
    (SOURCE_HKCU, winreg.HKEY_CURRENT_USER, _RUN_SUBKEY, False),
    (SOURCE_HKLM, winreg.HKEY_LOCAL_MACHINE, _RUN_SUBKEY, True),
    (SOURCE_HKLM32, winreg.HKEY_LOCAL_MACHINE, _RUN_SUBKEY_WOW64, True),
)

# StartupApproved: джерело -> (hive, підключ)
_APPROVED_BASE = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
_APPROVED_KEYS = {
    SOURCE_HKCU: (winreg.HKEY_CURRENT_USER, _APPROVED_BASE + r"\Run"),
    SOURCE_HKLM: (winreg.HKEY_LOCAL_MACHINE, _APPROVED_BASE + r"\Run"),
    SOURCE_HKLM32: (winreg.HKEY_LOCAL_MACHINE, _APPROVED_BASE + r"\Run32"),
    SOURCE_STARTUP_USER: (winreg.HKEY_CURRENT_USER, _APPROVED_BASE + r"\StartupFolder"),
    SOURCE_STARTUP_COMMON: (winreg.HKEY_LOCAL_MACHINE, _APPROVED_BASE + r"\StartupFolder"),
}
_STATE_ENABLED, _STATE_DISABLED = 2, 3

# теки зі старих версій (лише для міграції й щоб не показувати їх як записи)
_DISABLED_DIR_NAME = "Lagnix_Disabled"
_OLD_DISABLED_DIR_NAMES = (_DISABLED_DIR_NAME, "PulseFPS_Disabled")
_IGNORED_STARTUP_NAMES = {"desktop.ini"}

# Технічні ідентифікатори реєстру (напр. "MicrosoftEdgeAutoLaunch_a1b2c3d4e5")
# замінюємо на FileDescription/ProductName з exe, якщо назва схожа на один із цих шаблонів.
_TECHNICAL_NAME_RE = re.compile(
    r"_[0-9a-fA-F]{6,}$|^\{[0-9a-fA-F-]{36}\}$|^[a-z0-9]+(\.[a-z0-9]+){2,}$",
    re.IGNORECASE,
)

_HIGH_IMPACT_KEYWORDS = ("steam", "teams", "vanguard", "easyanticheat", "battleye", "faceit")
_MEDIUM_IMPACT_KEYWORDS = ("discord", "onedrive", "edge", "skype", "spotify", "slack", "zoom", "dropbox")
_IMPACT_HIGH, _IMPACT_MEDIUM, _IMPACT_LOW = "high", "medium", "low"  # підпис — t("autostart.impact.<id>")

_PROTECTED_NAME_KEYWORDS = ("securityhealth", "windowsdefender", "defender")

_ANTICHEAT_KEYWORDS = ("vanguard", "easyanticheat", "battleye", "faceit")
ANTICHEAT_WARNING = "autostart.anticheat_warning"  # ключ перекладу

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
    return dict(load_data().get("autostart_disabled", {}))


def _save_disabled_state(state: dict) -> None:
    update_data("autostart_disabled", state)


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


def _registry_source_info(source: str):
    for src, hive, subkey_path, requires_admin in _REGISTRY_SOURCES:
        if src == source:
            return hive, subkey_path, requires_admin
    return None


# ---------------------------------------------------------- StartupApproved

def _approved_value(state: int) -> bytes:
    """12 байт: стан (02/03) + 3 нульових байти + FILETIME (для вимкненого — зараз)."""
    filetime = int((time.time() + 11644473600) * 10_000_000) if state == _STATE_DISABLED else 0
    return struct.pack("<I", state) + struct.pack("<Q", filetime)


def _read_approved(source: str) -> dict[str, bool]:
    """name(lower) -> True, якщо Windows вважає запис вимкненим (непарний перший байт)."""
    hive, subkey = _APPROVED_KEYS[source]
    result: dict[str, bool] = {}
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    name, value, kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if kind == winreg.REG_BINARY and value:
                    result[name.lower()] = bool(value[0] & 1)
    except OSError:
        pass
    return result


def _write_approved(source: str, name: str, state: int) -> bool:
    hive, subkey = _APPROVED_KEYS[source]
    try:
        with winreg.CreateKeyEx(hive, subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, _approved_value(state))
        return True
    except OSError:
        return False


def _backup_approved_key(source: str) -> None:
    """.reg-бекап ключа StartupApproved перед зміною (спільний механізм із твіками)."""
    from core import tweaks
    tweaks.backup_registry_keys([_APPROVED_KEYS[source]])


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


read_version_field = _read_version_field
"Public name: an exe VERSIONINFO field (CompanyName, etc.), cached."


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
    """Реальний шлях до файлу запису (ярлика Startup або exe з команди Run)."""
    source = entry["source"]
    if source in (SOURCE_STARTUP_USER, SOURCE_STARTUP_COMMON):
        path = os.path.join(_startup_dir_for(source), entry["name"])
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
    """Усі програми автозапуску (реєстр і папки Startup) зі станом із StartupApproved."""
    entries = []
    approved = {source: _read_approved(source) for source in _APPROVED_KEYS}

    for source, hive, subkey_path, requires_admin in _REGISTRY_SOURCES:
        for name, command in _read_run_values(hive, subkey_path).items():
            entries.append({
                "id": _entry_id(source, name), "source": source, "name": name, "command": command,
                "enabled": not approved[source].get(name.lower(), False), "requires_admin": requires_admin,
            })

    for source in (SOURCE_STARTUP_USER, SOURCE_STARTUP_COMMON):
        directory = _startup_dir_for(source)
        for name in _list_startup_files(directory):
            entries.append({
                "id": _entry_id(source, name), "source": source, "name": name,
                "command": os.path.join(directory, name),
                "enabled": not approved[source].get(name.lower(), False),
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
        return False, t("autostart.err.no_location")
    try:
        subprocess.Popen(["explorer", "/select,", path])
        return True, ""
    except OSError as exc:
        return False, str(exc)


# ------------------------------------------------------------------ toggle

def _set_state(entry: dict, enabled: bool) -> tuple[bool, str]:
    source = entry["source"]
    if source not in _APPROVED_KEYS:
        return False, t("autostart.err.unknown_source")
    if requires_admin_for(source) and not is_admin():
        get_logger("core.autostart").error("No administrator rights for the change")
        return False, t("common.err.need_admin")
    _backup_approved_key(source)
    state = _STATE_ENABLED if enabled else _STATE_DISABLED
    if not _write_approved(source, entry["name"], state):
        return False, t("autostart.err.toggle")
    get_audit_logger().info("Autostart entry %s: %s (%s) — StartupApproved",
                            "enabled" if enabled else "disabled", entry["name"], source)
    return True, ""


def disable_entry(entry: dict) -> tuple[bool, str]:
    """Вимикає запис автозапуску через StartupApproved (сам запис/ярлик не чіпаємо)."""
    return _set_state(entry, False)


def enable_entry(entry: dict) -> tuple[bool, str]:
    """Вмикає запис автозапуску назад (StartupApproved -> 02)."""
    return _set_state(entry, True)


# --------------------------------------------------------------- міграція

def _remove_legacy_dir(path: str) -> None:
    """Видаляє ЛИШЕ нашу стару теку Lagnix_Disabled/PulseFPS_Disabled і лише порожню."""
    if os.path.basename(path) in _OLD_DISABLED_DIR_NAMES and os.path.isdir(path) and not os.listdir(path):
        os.rmdir(path)


def _migrate_startup_dir(source: str) -> set[str]:
    """Повертає ярлики зі старої теки на місце, позначаючи їх вимкненими.
    Результат — імена (lower), що лишилися в старій теці."""
    log = get_logger("core.autostart")
    directory = _startup_dir_for(source)
    left: set[str] = set()
    for dirname in _OLD_DISABLED_DIR_NAMES:
        old = os.path.join(directory, dirname)
        if not os.path.isdir(old):
            continue
        if requires_admin_for(source) and not is_admin():
            log.warning("Legacy folder %s not migrated: administrator rights needed", old)
            left.update(n.lower() for n in os.listdir(old))
            continue
        _backup_approved_key(source)
        for name in os.listdir(old):
            src, dst = os.path.join(old, name), os.path.join(directory, name)
            if not os.path.isfile(src):
                left.add(name.lower())
                continue
            if os.path.exists(dst):
                log.warning("Shortcut %s not restored: %s already exists, left in %s", name, dst, old)
                left.add(name.lower())
                continue
            # спершу «вимкнено», щоб повернений ярлик не запустився при наступному вході
            if not _write_approved(source, name, _STATE_DISABLED):
                log.error("Could not mark %s as disabled in StartupApproved, left in %s", name, old)
                left.add(name.lower())
                continue
            try:
                shutil.move(src, dst)
            except OSError:
                log.exception("Could not move %s back to %s", src, directory)
                left.add(name.lower())
                continue
            get_audit_logger().info("Legacy disabled shortcut restored: %s (%s), marked disabled", name, source)
        try:
            _remove_legacy_dir(old)
        except OSError:
            log.exception("Could not remove the empty folder %s", old)
    return left


def migrate_legacy() -> None:
    """0.9.4: повертає ярлики з Lagnix_Disabled і Run-записи з data.json на місце, позначаючи їх вимкненими."""
    log = get_logger("core.autostart")
    left = {s: _migrate_startup_dir(s) for s in (SOURCE_STARTUP_USER, SOURCE_STARTUP_COMMON)}

    records = _disabled_state()
    if not records:
        return
    remaining = dict(records)
    for entry_id, record in records.items():
        source, name = record.get("source"), record.get("name", "")
        if source in left:
            if name.lower() not in left[source]:
                del remaining[entry_id]  # ярлик уже на місці (або його немає ніде)
            continue
        info = _registry_source_info(source)
        if info is None:
            del remaining[entry_id]
            continue
        hive, subkey_path, requires_admin = info
        if requires_admin and not is_admin():
            log.warning("Legacy Run entry %s not restored: administrator rights needed", name)
            continue
        if name not in _read_run_values(hive, subkey_path) and not _write_run_value(hive, subkey_path, name, record["command"]):
            log.error("Could not restore the Run entry %s", name)
            continue
        _backup_approved_key(source)
        if _write_approved(source, name, _STATE_DISABLED):
            get_audit_logger().info("Legacy disabled Run entry restored: %s (%s), marked disabled", name, source)
            del remaining[entry_id]
        else:
            log.error("Could not mark %s as disabled in StartupApproved", name)
    if remaining != records:
        _save_disabled_state(remaining)
