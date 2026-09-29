"""Список встановлених програм із реєстру Windows (Uninstall) та їх видалення.

Видалення завжди виконується через офіційний UninstallString самої програми
(записаний її інсталятором) — файли програми ніколи не видаляються вручну.

Реєстр далеко не завжди містить EstimatedSize/InstallDate. Модуль доповнює
відсутні дані з інших джерел (розмір теки встановлення, маніфести бібліотек
Steam, дата створення теки) — усе позначене як "size_source" != "registry"
вважається приблизним і має показуватись у вкладці з префіксом "~".
"""

import json
import os
import re
import subprocess
import time
import winreg
from datetime import datetime

_UNINSTALL_ROOTS = (
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM32"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKCU"),
)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_STEAM_UNINSTALL_RE = re.compile(r"steam://uninstall/(\d+)", re.IGNORECASE)
_STEAM_INSTALLER_CACHE = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Installer")

_STEAM_KEY_RE = re.compile(r"Steam App (\d+)$", re.IGNORECASE)
_EPIC_MANIFESTS_DIR = os.path.join(
    os.environ.get("ProgramData", r"C:\ProgramData"), "Epic", "EpicGamesLauncher", "Data", "Manifests"
)

# Видавець (нижній регістр, префікс) -> магазин/лаунчер; Steam визначається окремо за appid.
_STORE_PUBLISHERS = (
    ("riot games", "Riot"),
    ("rockstar games", "Rockstar"),
    ("epic games", "Epic"),
)
_GAME_STORES = ("Steam", "Epic", "Riot", "Rockstar")
_LAUNCHER_NAME_RE = re.compile(r"launcher|client|epic online services|social club", re.IGNORECASE)
_SYSTEM_NAME_RE = re.compile(
    r"redistributable|runtime|driver|chipset|physx|\.net |framework|webview2|directx|vulkan|"
    r"visual c\+\+|windows (sdk|software development)|update for|management engine|hd audio",
    re.IGNORECASE,
)

_folder_size_cache: dict[str, int] = {}


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


def _folder_creation_date(folder: str):
    try:
        return datetime.fromtimestamp(os.path.getctime(folder)).date()
    except (OSError, OverflowError, ValueError):
        return None


def _guess_install_folder(entry_key, uninstall_string: str) -> str | None:
    location = _read_value(entry_key, "InstallLocation")
    if location and os.path.isdir(location):
        return location

    icon = _read_value(entry_key, "DisplayIcon")
    if icon:
        icon_path = icon.split(",")[0].strip().strip('"')
        folder = os.path.dirname(icon_path)
        if folder and os.path.isdir(folder) and not folder.lower().startswith(_STEAM_INSTALLER_CACHE.lower()):
            return folder

    stripped = uninstall_string.strip()
    if stripped.startswith('"'):
        end = stripped.find('"', 1)
        exe_path = stripped[1:end] if end != -1 else stripped[1:]
    else:
        exe_path = stripped.split(" ")[0]

    if os.path.basename(exe_path).lower() in ("msiexec.exe", "msiexec"):
        return None

    folder = os.path.dirname(exe_path)
    return folder if folder and os.path.isdir(folder) else None


# ------------------------------------------------------------------- steam

def _find_steam_install_path() -> str | None:
    candidates = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    )
    for hive, subkey, value_name in candidates:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
        except OSError:
            continue
        if value and os.path.isdir(value):
            return value
    return None


def _vdf_field(content: str, name: str) -> str | None:
    match = re.search(rf'"{re.escape(name)}"\s*"([^"]*)"', content, re.IGNORECASE)
    return match.group(1) if match else None


def _parse_steam_library_paths(steam_path: str) -> list[str]:
    vdf_path = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError:
        content = ""

    paths = []
    for match in re.finditer(r'"path"\s*"([^"]+)"', content, re.IGNORECASE):
        raw = match.group(1).replace("\\\\", "\\")
        if os.path.isdir(raw):
            paths.append(raw)

    if steam_path not in paths and os.path.isdir(steam_path):
        paths.insert(0, steam_path)
    return paths


def _get_steam_app_info() -> dict[str, dict]:
    """appid -> {"size_bytes", "install_folder"} з appmanifest_*.acf усіх бібліотек Steam."""
    steam_path = _find_steam_install_path()
    if not steam_path:
        return {}

    apps = {}
    for library in _parse_steam_library_paths(steam_path):
        steamapps_dir = os.path.join(library, "steamapps")
        if not os.path.isdir(steamapps_dir):
            continue

        try:
            entries = os.listdir(steamapps_dir)
        except OSError:
            continue

        for entry_name in entries:
            if not (entry_name.startswith("appmanifest_") and entry_name.endswith(".acf")):
                continue

            try:
                with open(os.path.join(steamapps_dir, entry_name), "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except OSError:
                continue

            appid = _vdf_field(content, "appid")
            installdir = _vdf_field(content, "installdir")
            if not appid or not installdir:
                continue

            try:
                size_bytes = int(_vdf_field(content, "SizeOnDisk") or 0)
            except ValueError:
                size_bytes = 0

            install_folder = os.path.join(steamapps_dir, "common", installdir)
            apps[appid] = {
                "size_bytes": size_bytes,
                "install_folder": install_folder if os.path.isdir(install_folder) else None,
            }

    return apps


def _get_epic_install_folders() -> set[str]:
    """Нормалізовані InstallLocation усіх ігор із маніфестів Epic Games Launcher."""
    folders = set()
    try:
        names = os.listdir(_EPIC_MANIFESTS_DIR)
    except OSError:
        return folders
    for name in names:
        if not name.endswith(".item"):
            continue
        try:
            with open(os.path.join(_EPIC_MANIFESTS_DIR, name), "r", encoding="utf-8", errors="ignore") as f:
                location = json.load(f).get("InstallLocation")
        except (OSError, ValueError):
            continue
        if location:
            folders.add(os.path.normcase(os.path.normpath(location)))
    return folders


def _detect_store(key_name: str, uninstall_string: str, publisher: str, install_folder: str | None,
                  epic_folders: set[str]) -> tuple[str | None, str | None]:
    """(магазин, steam_appid); магазин — "Steam"/"Epic"/"Riot"/"Rockstar" або None."""
    steam_match = _STEAM_UNINSTALL_RE.search(uninstall_string) or _STEAM_KEY_RE.search(key_name)
    if steam_match:
        return "Steam", steam_match.group(1)

    publisher_lc = publisher.lower()
    for prefix, store in _STORE_PUBLISHERS:
        if publisher_lc.startswith(prefix):
            return store, None

    if install_folder and os.path.normcase(os.path.normpath(install_folder)) in epic_folders:
        return "Epic", None
    return None, None


def _classify(name: str, publisher: str, store: str | None) -> str:
    """Категорія для діаграми: game / system / app / other (без видавця — невідомо що)."""
    if store in _GAME_STORES and not _LAUNCHER_NAME_RE.search(name):
        return "game"
    if _SYSTEM_NAME_RE.search(name):
        return "system"
    return "app" if publisher else "other"


def _parse_display_icon(raw) -> tuple[str, int] | None:
    """DisplayIcon: '"C:\\a\\b.exe",0' -> (шлях, індекс); порожнє/неіснуюче -> None."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    index = 0
    head, sep, tail = text.rpartition(",")
    if sep and re.fullmatch(r"\s*-?\d+\s*", tail):
        text, index = head, int(tail)
    path = os.path.expandvars(text.strip().strip('"'))
    return (path, index) if os.path.isfile(path) else None


# --------------------------------------------------------------- programs

def list_installed_programs() -> list[dict]:
    """Читає гілки Uninstall (HKLM, HKLM WOW6432Node, HKCU) і повертає видимі програми.

    Пропускає компоненти системи (SystemComponent=1), оновлення без власного
    запису (ParentKeyName) і записи без UninstallString — їх нічим видаляти.

    Для ігор Steam розмір і теку встановлення уточнює за appmanifest_*.acf
    відповідної бібліотеки. Якщо дати немає — бере дату створення теки.
    """
    steam_apps = _get_steam_app_info()
    epic_folders = _get_epic_install_folders()
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
                        uninstall_string = str(_read_value(entry_key, "UninstallString") or "")
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
                        size_source = "registry" if size_bytes > 0 else None

                        install_folder = _guess_install_folder(entry_key, uninstall_string)

                        publisher = str(_read_value(entry_key, "Publisher", "") or "")
                        store, steam_appid = _detect_store(
                            subkey_name, uninstall_string, publisher, install_folder, epic_folders
                        )
                        if steam_appid:
                            steam_info = steam_apps.get(steam_appid)
                            if steam_info:
                                if not size_bytes and steam_info["size_bytes"]:
                                    size_bytes = steam_info["size_bytes"]
                                    size_source = "steam"
                                if steam_info.get("install_folder"):
                                    install_folder = steam_info["install_folder"]

                        install_date = _parse_install_date(_read_value(entry_key, "InstallDate"))
                        if install_date is None and install_folder:
                            install_date = _folder_creation_date(install_folder)

                        programs.append({
                            "key": f"{hive_name}\\{subkey_name}",
                            "name": str(name),
                            "publisher": publisher,
                            "version": str(_read_value(entry_key, "DisplayVersion", "") or ""),
                            "size_bytes": size_bytes,
                            "size_source": size_source,
                            "install_date": install_date,
                            "uninstall_string": uninstall_string,
                            "install_folder": install_folder,
                            "store": store,
                            "steam_appid": steam_appid,
                            "category": _classify(str(name), publisher, store),
                            "display_icon": _parse_display_icon(_read_value(entry_key, "DisplayIcon")),
                        })
                except OSError:
                    continue

    programs.sort(key=lambda p: p["name"].lower())
    return programs


def cached_folder_size(path: str) -> int | None:
    return _folder_size_cache.get(os.path.normcase(os.path.abspath(path)))


def compute_folder_size(path: str) -> int:
    """Рекурсивно рахує розмір теки й кешує результат. Повільно для великих програм —
    викликати лише з фонового потоку, щоб не блокувати інтерфейс.
    """
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for file_name in files:
            try:
                total += os.path.getsize(os.path.join(root, file_name))
            except OSError:
                continue

    _folder_size_cache[os.path.normcase(os.path.abspath(path))] = total
    return total


def uninstall_program(uninstall_string: str) -> tuple[subprocess.Popen | None, str]:
    """Запускає офіційний UninstallString програми як є, без модифікацій.

    Повертає об'єкт процесу, щоб виклик міг у фоні дочекатись завершення
    деінсталятора (process.poll()), або None з описом помилки.
    """
    if not uninstall_string:
        return None, "Немає команди видалення"

    try:
        process = subprocess.Popen(uninstall_string, shell=True, creationflags=_NO_WINDOW)
        return process, ""
    except OSError as exc:
        return None, str(exc)


# ------------------------------------------------------- uninstall + wait

STEAM_UNINSTALL_TIMEOUT_S = 300
_POLL_INTERVAL_S = 1.0


def is_registered(key: str) -> bool:
    """Чи є ще запис програми в реєстрі (ключ виду "HKLM32\\<subkey>" з list_installed_programs)."""
    hive_name, _, subkey = key.partition("\\")
    for hive, root_path, name in _UNINSTALL_ROOTS:
        if name != hive_name:
            continue
        try:
            with winreg.OpenKey(hive, f"{root_path}\\{subkey}"):
                return True
        except OSError:
            return False
    return False


def uninstall_and_wait(program: dict, should_abort=lambda: False) -> tuple[bool, str]:
    """Запускає видалення й блокує потік, доки запис програми не зникне з реєстру.

    Ігри Steam видаляються через Steam (steam://uninstall/<appid>) — він сам
    показує вікно підтвердження й чистить бібліотеку; решта — через офіційний
    UninstallString. Викликати лише з фонового потоку. Повертає (видалено, помилка);
    помилка порожня, якщо видалення просто скасовано/не завершено.
    """
    key = program["key"]
    appid = program.get("steam_appid")

    if appid:
        try:
            os.startfile(f"steam://uninstall/{appid}")
        except OSError as exc:
            return False, str(exc)
        deadline = time.monotonic() + STEAM_UNINSTALL_TIMEOUT_S
        while time.monotonic() < deadline and not should_abort():
            if not is_registered(key):
                return True, ""
            time.sleep(_POLL_INTERVAL_S)
        return not is_registered(key), ""

    process, error = uninstall_program(program["uninstall_string"])
    if process is None:
        return False, error
    while process.poll() is None and not should_abort():
        time.sleep(0.4)
    return not is_registered(key), ""
