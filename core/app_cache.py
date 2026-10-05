"""Автопошук кешу програм для блоку «Кеш програм» вкладки «Очищення».

Замість фіксованого списку програм модуль обходить %LOCALAPPDATA%, %APPDATA%
і LocalCache пакетів Microsoft Store (до MAX_DEPTH рівнів) і бере ЛИШЕ папки
з білого списку назв (_CACHE_NAMES, _CACHE_PAIRS, logs — тільки *.log).
Папки з даними користувача (Local Storage, IndexedDB, Cookies, Login Data,
Session Storage, Network, будь-що з profile/save/config/settings у назві)
пропускаються разом з усім вмістом — і під час пошуку, і всередині знайдених
папок кешу. Браузери та теки, які вже покриває інший блок вкладки (Temp,
CrashDumps, кеш шейдерів, Microsoft\\Windows), не скануються взагалі.

Шляхи лаунчерів (Steam, Epic, Riot, Battle.net, EA, Ubisoft) беруться з
реєстру. Знайдене групується за програмою; результат кешується в пам'яті до
наступного повного scan(). Очищення приймає лише ключ групи з цього кешу —
довільний шлях ззовні не приймається.
"""

import ctypes
import os
import re
import subprocess
import threading
import time
import winreg
import xml.etree.ElementTree as ET

import psutil

from core import process_control
from core.admin import is_admin
from core.logging_setup import get_audit_logger, get_logger
from core.system_processes import is_hidden, is_protected
from core.i18n import t

MAX_DEPTH = 4
SMALL_BYTES = 10 * 1024 * 1024

DEPOT_NOTE = "app_cache.depot_note"  # ключ перекладу (UI показує t(folder["note"]))

_CACHE_NAMES = {
    "cache", "code cache", "gpucache", "dawncache", "dawngraphitecache", "shadercache",
    "grshadercache", "crashdumps", "htmlcache", "webcache", "media_cache",
}
_CACHE_PAIRS = {("crashpad", "reports"), ("service worker", "cachestorage")}
_LOGS_NAME = "logs"
_LOG_EXTS = (".log",)
_LAUNCHER_LOG_EXTS = (".log", ".txt")

_FORBIDDEN_NAMES = {"local storage", "indexeddb", "cookies", "login data", "session storage", "network"}
_FORBIDDEN_PARTS = ("profile", "save", "config", "settings")

# Відносно кореня (%LOCALAPPDATA% / %APPDATA% / LocalCache\Local|Roaming), нижній регістр.
_BROWSER_DIRS = (
    "google\\chrome", "google\\chrome beta", "google\\chrome dev", "google\\chrome sxs",
    "microsoft\\edge", "microsoft\\edge beta", "microsoft\\edge dev", "microsoft\\edge sxs",
    "mozilla", "opera software", "bravesoftware", "vivaldi", "yandex", "chromium",
    "thebrowsercompany", "waterfox", "librewolf", "comodo", "centbrowser", "maxthon",
)
_COVERED_DIRS = (
    "temp", "packages", "crashdumps", "d3dscache", "nvidia", "nvidia corporation",
    "microsoft\\windows", "microsoft\\windowsapps", "microsoft\\edgewebview", "microsoft\\edgeupdate",
    "programs", "application data", "history", "temporary internet files",
)
# Компоненти Windows і облікових записів Microsoft: напр. TokenBroker\Cache — це збережені
# токени входу, а не кеш, який можна безпечно видалити.
_SYSTEM_DIRS = (
    "microsoft\\tokenbroker", "microsoft\\identitycrl", "microsoft\\oneauth", "microsoft\\credentials",
    "microsoft\\protect", "microsoft\\vault", "microsoft\\crypto", "microsoft\\systemcertificates",
    "microsoft\\input", "microsoft\\ido", "microsoft\\internet explorer", "microsoft\\clr_v4.0",
    "windowsoobeapphost", "connecteddevicesplatform", "comms", "microsoft\\windows defender",
)
_EXCLUDED = _BROWSER_DIRS + _COVERED_DIRS + _SYSTEM_DIRS

# Теки видавців, у яких кожна підтека — окрема програма.
_VENDOR_DIRS = {"microsoft", "google", "adobe", "jetbrains", "apple", "amd", "intel", "logitech"}
_STORE_EXCLUDED = (
    "microsoft.windows", "microsoftwindows.", "windows.", "microsoft.microsoftedge",
    "thebrowsercompany.", "mozilla.", "opera.", "bravesoftware.",
)
# Підтеки інсталяцій лаунчерів, де лежать самі ігри — туди не заходимо.
_INSTALL_SKIP = {"games", "steamapps", "common"}
_GENERIC_DESCRIPTIONS = {"electron", "chromium", "squirrel", "application", "app", "launcher", "cefsharp"}

_REPARSE_POINT = 0x400
_PKG_REPOSITORY = (
    r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages"
)

_lock = threading.Lock()
_groups: dict[str, dict] | None = None
_log = get_logger("core.app_cache")


# ------------------------------------------------------------------ helpers

def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def _is_forbidden(name: str) -> bool:
    lowered = name.lower()
    return lowered in _FORBIDDEN_NAMES or any(part in lowered for part in _FORBIDDEN_PARTS)


def _match_kind(parent: str, name: str) -> str | None:
    if name in _CACHE_NAMES or (parent, name) in _CACHE_PAIRS:
        return "cache"
    if name == _LOGS_NAME:
        return "logs"
    return None


def _under(rel: str, prefixes) -> bool:
    return any(rel == p or rel.startswith(p + "\\") for p in prefixes)


def _is_reparse(stat_result) -> bool:
    return bool(getattr(stat_result, "st_file_attributes", 0) & _REPARSE_POINT)


def _subdirs(path: str):
    """Підтеки без символьних посилань і junction-ів (у AppData є петлі на кшталт «Application Data»)."""
    try:
        with os.scandir(path) as it:
            entries = list(it)
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False) and not _is_reparse(entry.stat(follow_symlinks=False)):
                yield entry
        except OSError:
            continue


def _safe_dir(path: str) -> bool:
    try:
        return os.path.isdir(path) and not _is_reparse(os.lstat(path))
    except OSError:
        return False


def _generic_dirs() -> set[str]:
    env = os.environ
    paths = {
        env.get("LOCALAPPDATA", ""), env.get("APPDATA", ""), env.get("ProgramData", ""),
        env.get("ProgramFiles", ""), env.get("ProgramFiles(x86)", ""), env.get("USERPROFILE", ""),
        env.get("SystemRoot", ""), os.path.join(env.get("LOCALAPPDATA", ""), "Programs"),
    }
    return {os.path.normcase(os.path.normpath(p)) for p in paths if p}


def _is_specific(path: str | None) -> bool:
    """Тека конкретної програми, а не спільна (Program Files, AppData, корінь диска)."""
    if not path:
        return False
    normalized = os.path.normcase(os.path.normpath(path))
    system_root = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))
    if normalized in _generic_dirs() or normalized.startswith(system_root + "\\"):
        return False
    drive, rest = os.path.splitdrive(normalized)
    return bool(rest.strip("\\"))


def _pretty(folder_name: str) -> str:
    if folder_name.islower():
        return re.sub(r"[-_]+", " ", folder_name).strip().title()
    return folder_name


def _reg_str(hive, path: str, value: str, view: int = 0) -> str | None:
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view) as key:
            data, _ = winreg.QueryValueEx(key, value)
    except OSError:
        return None
    return str(data).strip() if data else None


def _command_exe(command: str | None) -> str | None:
    """Перший (виконуваний) токен командного рядка з реєстру."""
    if not command:
        return None
    command = os.path.expandvars(command.strip())
    if command.startswith('"'):
        end = command.find('"', 1)
        path = command[1:end] if end != -1 else command[1:]
    else:
        match = re.match(r"(.+?\.exe)\b", command, re.IGNORECASE)
        path = match.group(1) if match else command.split(" ")[0]
    path = os.path.normpath(path.replace("/", "\\"))
    return path if os.path.isfile(path) else None


def _protocol_exe(protocol: str) -> str | None:
    return _command_exe(_reg_str(winreg.HKEY_CLASSES_ROOT, rf"{protocol}\shell\open\command", ""))


def _guess_exe(folder: str | None, name: str) -> str | None:
    if not folder or not os.path.isdir(folder):
        return None
    from core.app_icons import _guess_exe as guess
    exe = guess(folder, name)
    if exe:
        return exe
    # Squirrel-інсталяції (Discord, Slack...): exe лежить у найновішій app-X.Y.Z.
    try:
        versions = sorted((d for d in os.listdir(folder) if d.lower().startswith("app-")), reverse=True)
    except OSError:
        return None
    for version in versions:
        exe = guess(os.path.join(folder, version), name)
        if exe:
            return exe
    return None


def _version_field(exe: str, field: str) -> str | None:
    try:
        from core.autostart import read_version_field
        value = read_version_field(exe, field)
    except Exception:
        return None
    value = (value or "").strip()
    if not value or len(value) > 40 or value.lower() in _GENERIC_DESCRIPTIONS or value.lower().endswith(".exe"):
        return None
    return value


_INSTALLER_EXE_RE = re.compile(r"unins|install|setup|update|crash|helper|report|redist", re.IGNORECASE)


def _clean_program_name(name: str) -> str:
    name = re.sub(r"^uninstall\s+", "", name, flags=re.IGNORECASE)
    user = os.environ.get("USERNAME", "")
    if user:
        name = re.sub(rf"\s+for\s+{re.escape(user)}$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*\((x64|x86|64-bit|32-bit|64 bit|32 bit)[^)]*\)", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+(version\s+)?v?\d+(\.\d+)+.*$", "", name, flags=re.IGNORECASE)
    return name.strip() or name


def _indirect_string(text: str | None) -> str | None:
    """Розгортає «@{пакет?ms-resource://...}» у людську назву пакета Store."""
    if not text:
        return None
    if not text.startswith("@"):
        return text
    buf = ctypes.create_unicode_buffer(512)
    try:
        result = ctypes.windll.shlwapi.SHLoadIndirectString(text, buf, len(buf), None)
    except (AttributeError, OSError):
        return None
    return buf.value if result == 0 and buf.value else None


def _new_folder(path: str, kind: str, label: str, exts=None, note: str | None = None) -> dict:
    if exts is None and kind == "logs":
        exts = _LOG_EXTS
    return {
        "path": os.path.normpath(path), "kind": kind, "label": label, "exts": exts, "note": note,
        "size_bytes": 0, "file_count": 0,
    }


def _new_group(key: str) -> dict:
    return {
        "key": key, "name": None, "folders": [], "app_dirs": set(), "dir_names": set(),
        "homes": set(), "exe": None, "process_names": set(), "stems": set(),
        "package": None, "aumid": None, "launcher": False, "shutdown_args": None,
        "size_bytes": 0, "file_count": 0, "running": False,
    }


# --------------------------------------------------------------- discovery

def _find_caches(base: str, rel: str, depth: int, out: list, skip_names=frozenset()) -> None:
    """Шукає папки з білого списку під base; знайдену папку не розкриває далі."""
    parent = os.path.basename(base).lower()
    for entry in _subdirs(base):
        name = entry.name.lower()
        if _is_forbidden(name) or name in skip_names:
            continue
        child_rel = f"{rel}\\{name}" if rel else name
        if _under(child_rel, _EXCLUDED):
            continue
        kind = _match_kind(parent, name)
        if kind:
            label = f"{os.path.basename(base)}\\{entry.name}" if (parent, name) in _CACHE_PAIRS else entry.name
            out.append((entry.path, kind, label))
            continue
        if depth < MAX_DEPTH:
            _find_caches(entry.path, child_rel, depth + 1, out, skip_names)


def _top_level_units(root: str) -> list:
    units = []
    for entry in _subdirs(root):
        name = entry.name.lower()
        if _is_forbidden(name) or _under(name, _EXCLUDED) or _match_kind("", name):
            continue
        units.append(entry)
    return units


def _add_appdata_top(groups: dict, top, launcher_by_dir: dict, key_prefix: str = "app:") -> None:
    found = []
    _find_caches(top.path, top.name.lower(), 2, found)
    if not found:
        return

    spec_key = launcher_by_dir.get(top.name.lower())
    if spec_key and spec_key in groups:
        group = groups[spec_key]
        group["app_dirs"].add(top.path)
        group["folders"].extend(_new_folder(p, k, label) for p, k, label in found)
        return

    rels = [os.path.relpath(path, top.path).split(os.sep) for path, _k, _l in found]
    direct = any(len(parts) == 1 for parts in rels)
    seconds = {parts[0].lower() for parts in rels if len(parts) >= 2}
    split = top.name.lower() in _VENDOR_DIRS or (not direct and len(seconds) >= 2)

    for (path, kind, label), parts in zip(found, rels):
        app_dir = os.path.join(top.path, parts[0]) if split and len(parts) >= 2 else top.path
        dir_name = os.path.basename(app_dir)
        key = key_prefix + _norm(dir_name)
        group = groups.setdefault(key, _new_group(key))
        group["app_dirs"].add(app_dir)
        group["dir_names"].add(dir_name)
        group["folders"].append(_new_folder(path, kind, label))


def _store_packages_index() -> dict[str, list[str]]:
    """Сімейство пакета (нижній регістр) -> повні назви зареєстрованих версій."""
    index: dict[str, list[str]] = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PKG_REPOSITORY)
    except OSError:
        return index
    with root:
        i = 0
        while True:
            try:
                full = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            name, _, rest = full.partition("_")
            publisher = rest.rpartition("__")[2]
            if name and publisher:
                index.setdefault(f"{name}_{publisher}".lower(), []).append(full)
    return index


def _apply_package_info(group: dict, family: str, index: dict) -> None:
    fulls = sorted(index.get(family.lower(), []))
    display = None
    for full in reversed(fulls):
        path = f"{_PKG_REPOSITORY}\\{full}"
        display = display or _indirect_string(_reg_str(winreg.HKEY_CURRENT_USER, path, "DisplayName"))
        root_folder = _reg_str(winreg.HKEY_CURRENT_USER, path, "PackageRootFolder")
        if not root_folder:
            continue
        group["homes"].add(root_folder)
        try:
            tree = ET.parse(os.path.join(root_folder, "AppxManifest.xml"))
        except (OSError, ET.ParseError):
            continue
        for element in tree.getroot().iter():
            if element.tag.rsplit("}", 1)[-1] == "Application" and element.get("Executable"):
                exe = os.path.join(root_folder, element.get("Executable"))
                group["exe"] = group["exe"] or exe
                group["stems"].add(_norm(os.path.splitext(os.path.basename(exe))[0]))
                if element.get("Id"):
                    group["aumid"] = group["aumid"] or f"{family}!{element.get('Id')}"
                break
        if group["exe"]:
            break

    if display and not display.startswith("ms-resource:"):
        group["name"] = display
    else:
        short = family.rpartition("_")[0].split(".")[-1]
        group["name"] = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", short) or family


def _add_package(groups: dict, package, index: dict) -> None:
    family = package.name
    local_cache = os.path.join(package.path, "LocalCache")
    found = []
    for child in _subdirs(local_cache):
        name = child.name.lower()
        if name in ("local", "roaming"):
            # Віртуалізовані AppData\Local і AppData\Roaming пакета.
            for top in _top_level_units(child.path):
                _find_caches(top.path, top.name.lower(), 2, found)
        elif not _is_forbidden(name):
            kind = _match_kind("", name)
            if kind:
                found.append((child.path, kind, child.name))
            else:
                _find_caches(child.path, name, 2, found)
    if not found:
        return

    key = "pkg:" + family.lower()
    group = groups.setdefault(key, _new_group(key))
    group["package"] = family.lower()
    group["app_dirs"].add(package.path)
    group["folders"].extend(_new_folder(p, k, label) for p, k, label in found)
    _apply_package_info(group, family, index)


# --------------------------------------------------------------- launchers

def _steam_spec(programs: list[dict]) -> dict | None:
    path = None
    for hive, subkey, value in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ):
        candidate = _reg_str(hive, subkey, value)
        if candidate and os.path.isdir(candidate):
            path = os.path.normpath(candidate.replace("/", "\\"))
            break
    if not path:
        return None

    exe = _command_exe(_reg_str(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamExe"))
    exe = exe or (os.path.join(path, "steam.exe") if os.path.isfile(os.path.join(path, "steam.exe")) else None)
    return {
        "key": "launcher:steam", "name": "Steam", "exe": exe, "homes": {path},
        "process_names": {"steam.exe", "steamwebhelper.exe"},
        "data_dirs": {"steam"},
        "folders": [
            _new_folder(os.path.join(path, "config", "htmlcache"), "cache", "htmlcache"),
            _new_folder(os.path.join(path, "appcache", "httpcache"), "cache", "appcache\\httpcache"),
            _new_folder(os.path.join(path, "depotcache"), "cache", "depotcache", note=DEPOT_NOTE),
            _new_folder(os.path.join(path, "logs"), "logs", "logs", exts=_LAUNCHER_LOG_EXTS),
        ],
        "install_scan": [],
        "shutdown_args": [exe, "-shutdown"] if exe else None,
    }


def _find_program(programs: list[dict], pattern: str) -> dict | None:
    regex = re.compile(pattern, re.IGNORECASE)
    return next((p for p in programs if regex.search(p["name"])), None)


def _ancestor_named(path: str | None, name: str) -> str | None:
    while path:
        if os.path.basename(path).lower() == name:
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent
    return None


def _first_existing_exe(folder: str | None, names) -> str | None:
    if not folder:
        return None
    for name in names:
        candidate = os.path.join(folder, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _other_launcher_specs(programs: list[dict]) -> list[dict]:
    specs = []

    # Epic Games Launcher: протокол com.epicgames.launcher -> exe; сканується лише тека Launcher,
    # бо поруч можуть лежати самі ігри.
    exe = _protocol_exe("com.epicgames.launcher")
    program = _find_program(programs, r"^epic games launcher$")
    if not exe and program and program.get("install_folder"):
        for arch in ("Win64", "Win32"):
            exe = exe or _first_existing_exe(
                os.path.join(program["install_folder"], "Launcher", "Portal", "Binaries", arch),
                ("EpicGamesLauncher.exe",),
            )
    if exe or program:
        launcher_dir = _ancestor_named(exe, "launcher")
        specs.append({
            "key": "launcher:epic", "name": "Epic Games Launcher", "exe": exe,
            "homes": {launcher_dir} if launcher_dir else set(),
            "process_names": {"epicgameslauncher.exe", "epicwebhelper.exe"},
            "data_dirs": {"epicgameslauncher"},
            "install_scan": [launcher_dir] if launcher_dir else [],
        })

    # Riot Client: шлях RiotClientServices.exe з записів про видалення ігор Riot або з протоколу.
    exe = _protocol_exe("riotclient")
    if not exe:
        for program in programs:
            if program.get("publisher", "").lower().startswith("riot games"):
                exe = _command_exe(program.get("uninstall_string"))
                if exe and os.path.basename(exe).lower() == "riotclientservices.exe":
                    break
                exe = None
    if exe:
        folder = os.path.dirname(exe)
        specs.append({
            "key": "launcher:riot", "name": "Riot Client", "exe": exe, "homes": {folder},
            "process_names": {"riotclientservices.exe", "riot client.exe", "riotclientux.exe",
                              "riotclientuxrender.exe", "riotclientcrashhandler.exe"},
            "data_dirs": {"riot games", "riot client", "riot-client-ux"},
            "install_scan": [folder],
        })

    # Battle.net
    icon = _reg_str(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Blizzard Entertainment\Battle.net\Capabilities",
                    "ApplicationIcon", winreg.KEY_WOW64_32KEY)
    exe = _command_exe(icon.rsplit(",", 1)[0] if icon else None) or _protocol_exe("battlenet")
    program = _find_program(programs, r"^battle\.net$")
    folder = os.path.dirname(exe) if exe else (program or {}).get("install_folder")
    exe = exe or _first_existing_exe(folder, ("Battle.net Launcher.exe", "Battle.net.exe"))
    if folder and os.path.isdir(folder):
        specs.append({
            "key": "launcher:battlenet", "name": "Battle.net", "exe": exe, "homes": {folder},
            "process_names": {"battle.net.exe", "battle.net launcher.exe"},
            "data_dirs": {"battle.net", "blizzard entertainment"},
            "install_scan": [folder],
        })

    # EA app
    exe = _command_exe(_reg_str(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Electronic Arts\EA Desktop", "ClientPath"))
    folder = _reg_str(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Electronic Arts\EA Desktop", "InstallLocation")
    program = _find_program(programs, r"^(ea app|ea desktop)$")
    folder = folder or (os.path.dirname(exe) if exe else None) or (program or {}).get("install_folder")
    if not exe and folder:
        exe = _first_existing_exe(folder, ("EADesktop.exe",)) or _first_existing_exe(
            os.path.join(folder, "EA Desktop"), ("EADesktop.exe",))
    if folder and os.path.isdir(folder):
        specs.append({
            "key": "launcher:ea", "name": "EA app", "exe": exe, "homes": {folder},
            "process_names": {"eadesktop.exe"},
            "data_dirs": {"electronic arts", "eadesktop"},
            "install_scan": [folder],
        })

    # Ubisoft Connect: InstallDir з реєстру; підтеку games (там самі ігри) не скануємо.
    folder = None
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        folder = folder or _reg_str(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Ubisoft\Launcher", "InstallDir", view)
    exe = _protocol_exe("uplay")
    folder = os.path.normpath(folder) if folder else (os.path.dirname(exe) if exe else None)
    exe = exe or _first_existing_exe(folder, ("UbisoftConnect.exe", "upc.exe"))
    if folder and os.path.isdir(folder):
        specs.append({
            "key": "launcher:ubisoft", "name": "Ubisoft Connect", "exe": exe, "homes": {folder},
            "process_names": {"upc.exe", "ubisoftconnect.exe", "uplaywebcore.exe"},
            "data_dirs": {"ubisoft game launcher"},
            "install_scan": [folder],
        })

    return specs


def _launcher_group(spec: dict) -> dict:
    group = _new_group(spec["key"])
    # Процеси лаунчера — лише за назвами й exe: у теці встановлення (D:\Steam, Ubisoft\games)
    # лежать і самі ігри, а «Закрити й очистити» не повинно вбивати запущену гру.
    group.update(
        name=spec["name"], exe=spec.get("exe"), launcher=True,
        process_names=set(spec["process_names"]), shutdown_args=spec.get("shutdown_args"),
    )
    group["folders"].extend(f for f in spec.get("folders", []) if _safe_dir(f["path"]))
    for folder in spec.get("install_scan", []):
        if not _is_specific(folder) or not os.path.isdir(folder):
            continue
        found = []
        _find_caches(folder, "", 1, found, skip_names=_INSTALL_SKIP)
        for path, kind, label in found:
            exts = _LAUNCHER_LOG_EXTS if kind == "logs" else None
            group["folders"].append(_new_folder(path, kind, label, exts=exts))
    return group


# ------------------------------------------------------------ identify

def _program_index(programs: list[dict]) -> dict[str, dict]:
    index = {}
    for program in programs:
        index.setdefault(_norm(program["name"]), program)
        folder = program.get("install_folder")
        if folder and _is_specific(folder):
            index.setdefault(_norm(os.path.basename(folder.rstrip("\\/"))), program)
    return index


def _identify(group: dict, program_index: dict, processes: list[dict]) -> None:
    """Назва, exe (для іконки й перезапуску) і «домашні» теки звичайної програми."""
    local = os.environ.get("LOCALAPPDATA", "")
    bases = [local, os.path.join(local, "Programs"),
             os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")]

    group["homes"].update(group["app_dirs"])
    for dir_name in group["dir_names"]:
        group["stems"].add(_norm(dir_name))
        for base in bases:
            candidate = os.path.join(base, dir_name) if base else ""
            if candidate and os.path.isdir(candidate):
                group["homes"].add(candidate)

    program = None
    for stem in group["stems"]:
        program = program_index.get(stem)
        if program is None and len(stem) >= 6:
            program = next((p for k, p in program_index.items() if k.startswith(stem)), None)
        if program:
            break

    exe = None
    if program:
        if _is_specific(program.get("install_folder")):
            group["homes"].add(program["install_folder"])
        icon = program.get("display_icon")
        if icon and icon[0].lower().endswith(".exe") and not _INSTALLER_EXE_RE.search(os.path.basename(icon[0])):
            exe = icon[0]
        exe = exe or _guess_exe(program.get("install_folder"), program["name"])

    if not exe:
        for proc in processes:
            if proc["exe"] and _norm(os.path.splitext(proc["name"])[0]) in group["stems"]:
                exe = proc["exe_raw"]
                break
    if not exe:
        for home in sorted(group["homes"]):
            exe = _guess_exe(home, next(iter(group["dir_names"]), ""))
            if exe:
                break

    group["exe"] = exe
    if exe and _is_specific(os.path.dirname(exe)):
        group["homes"].add(os.path.dirname(exe))

    name = _clean_program_name(program["name"]) if program else None
    if not name and exe:
        name = _version_field(exe, "FileDescription") or _version_field(exe, "ProductName")
    group["name"] = name or _pretty(sorted(group["dir_names"])[0] if group["dir_names"] else group["key"])


# ----------------------------------------------------------- processes

def _process_table() -> list[dict]:
    own = os.getpid()
    table = []
    for proc in psutil.process_iter(["pid", "name", "exe", "ppid", "create_time"], ad_value=None):
        info = proc.info
        name = info.get("name") or ""
        if info["pid"] == own or not name or is_hidden(name) or is_protected(name):
            continue
        exe = info.get("exe") or ""
        table.append({"pid": info["pid"], "ppid": info.get("ppid"), "name": name.lower(),
                      "exe": exe.lower(), "exe_raw": exe, "create_time": info.get("create_time")})
    return table


def _proc_matches(group: dict, proc: dict) -> bool:
    if proc["name"] in group["process_names"]:
        return True
    exe = proc["exe"]
    if exe:
        if group["exe"] and exe == group["exe"].lower():
            return True
        if any(exe.startswith(os.path.normpath(home).lower() + "\\") for home in group["homes"]):
            return True
        package = group.get("package")
        if package and "\\windowsapps\\" in exe:
            folder = exe.split("\\windowsapps\\", 1)[1].split("\\", 1)[0]
            name, _, publisher = package.rpartition("_")
            if folder.startswith(name + "_") and folder.endswith("__" + publisher):
                return True
    stem = _norm(os.path.splitext(proc["name"])[0])
    return len(stem) >= 3 and stem in group["stems"]


def _group_processes(group: dict, table: list[dict]) -> list[dict]:
    return [proc for proc in table if _proc_matches(group, proc)]


# --------------------------------------------------------------- sizing

def _walk_files(path: str, exts):
    """(шлях, розмір) файлів у path; заборонені підтеки, symlink-и й junction-и пропускаються."""
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
                if _is_reparse(st):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if not _is_forbidden(entry.name):
                        stack.append(entry.path)
                    continue
            except OSError:
                continue
            lowered = entry.name.lower()
            if lowered in _FORBIDDEN_NAMES:
                continue
            if exts is None or lowered.endswith(exts):
                yield entry.path, st.st_size


def _measure(group: dict) -> None:
    total = count = 0
    for folder in group["folders"]:
        size = files = 0
        if _safe_dir(folder["path"]):
            for _path, file_size in _walk_files(folder["path"], folder["exts"]):
                size += file_size
                files += 1
        folder["size_bytes"], folder["file_count"] = size, files
        total += size
        count += files
    group["size_bytes"], group["file_count"] = total, count


def _dedupe_folders(groups: dict) -> None:
    """Одна тека не рахується двічі (напр. і як тека лаунчера, і як знайдена в AppData)."""
    seen: list[str] = []
    for group in groups.values():
        unique = []
        for folder in group["folders"]:
            path = os.path.normcase(folder["path"])
            if any(path == s or path.startswith(s + "\\") or s.startswith(path + "\\") for s in seen):
                continue
            seen.append(path)
            unique.append(folder)
        group["folders"] = unique


# ------------------------------------------------------------ public API

def _public(group: dict) -> dict:
    return {
        "key": group["key"],
        "name": group["name"],
        "exe": group["exe"],
        "launcher": group["launcher"],
        "running": group["running"],
        "size_bytes": group["size_bytes"],
        "file_count": group["file_count"],
        "folders": [dict(f) for f in group["folders"] if f["file_count"]],
    }


def _sorted_public(groups: dict) -> list[dict]:
    visible = [_public(g) for g in groups.values() if g["file_count"] > 0]
    visible.sort(key=lambda g: (-g["size_bytes"], g["name"].lower()))
    return visible


def is_small(group: dict) -> bool:
    return group["size_bytes"] < SMALL_BYTES


def get_cached() -> list[dict] | None:
    """Результат останнього повного сканування (None — ще не сканували)."""
    with _lock:
        return None if _groups is None else _sorted_public(_groups)


def scan(progress_cb=None, stop_event: threading.Event | None = None) -> list[dict] | None:
    """Повне сканування. progress_cb(частка 0..1, текст) — з фонового потоку.
    Повертає відсортовані за розміром групи або None, якщо перервано через stop_event."""
    global _groups
    started = time.perf_counter()

    def report(done, total, text):
        if progress_cb:
            progress_cb(min(done / max(total, 1), 1.0), text)

    def stopped():
        return stop_event is not None and stop_event.is_set()

    report(0, 1, t("app_cache.progress.registry"))
    try:
        from core.installed_programs import list_installed_programs
        programs = list_installed_programs()
    except Exception:
        _log.exception("Failed to read the list of installed programs")
        programs = []

    specs = []
    for builder in (_steam_spec, _other_launcher_specs):
        try:
            result = builder(programs)
        except Exception:
            _log.exception("Launcher detection error (%s)", builder.__name__)
            continue
        specs.extend(result if isinstance(result, list) else [result] if result else [])

    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    units = []
    for root in (local, roaming):
        if root and os.path.isdir(root):
            units.extend(("appdata", entry) for entry in _top_level_units(root))
    packages_dir = os.path.join(local, "Packages") if local else ""
    for entry in _subdirs(packages_dir) if packages_dir else ():
        if not entry.name.lower().startswith(_STORE_EXCLUDED) and os.path.isdir(os.path.join(entry.path, "LocalCache")):
            units.append(("package", entry))

    total = len(specs) + len(units) + 2
    done = 0
    groups: dict[str, dict] = {}
    launcher_by_dir = {}

    for spec in specs:
        if stopped():
            return None
        report(done, total, t("app_cache.progress.launcher", name=spec['name']))
        groups[spec["key"]] = _launcher_group(spec)
        for data_dir in spec.get("data_dirs", ()):
            launcher_by_dir[data_dir] = spec["key"]
        done += 1

    package_index = _store_packages_index()
    for kind, entry in units:
        if stopped():
            return None
        report(done, total, entry.name)
        try:
            if kind == "appdata":
                _add_appdata_top(groups, entry, launcher_by_dir)
            else:
                _add_package(groups, entry, package_index)
        except Exception:
            _log.exception("Scan error %s", entry.path)
        done += 1

    report(done, total, t("app_cache.progress.detecting"))
    _dedupe_folders(groups)
    table = _process_table()
    program_index = _program_index(programs)
    for group in groups.values():
        if not group["launcher"] and not group["package"]:
            try:
                _identify(group, program_index, table)
            except Exception:
                _log.exception("Program detection error %s", group["key"])
                group["name"] = group["name"] or group["key"].split(":", 1)[-1]
        group["name"] = group["name"] or group["key"].split(":", 1)[-1]
    done += 1

    for group in groups.values():
        if stopped():
            return None
        report(done, total, t("app_cache.progress.sizing", name=group['name']))
        _measure(group)
        group["running"] = bool(_group_processes(group, table)) if group["file_count"] else False
    done += 1

    with _lock:
        _groups = groups
    _log.info("App cache: %d groups in %.1f s", sum(1 for g in groups.values() if g["file_count"]),
              time.perf_counter() - started)
    report(total, total, t("app_cache.progress.done"))
    return _sorted_public(groups)


def rescan_groups(keys: list[str]) -> list[dict]:
    """Перераховує розмір і стан «запущена» лише для вказаних груп; повертає весь список."""
    with _lock:
        groups = _groups
    if groups is None:
        return []
    table = _process_table()
    for key in keys:
        group = groups.get(key)
        if group:
            _measure(group)
            group["running"] = bool(_group_processes(group, table))
    with _lock:
        return _sorted_public(groups)


def refresh_running() -> list[dict]:
    """Оновлює лише стан «запущена» для всіх груп (дешево — без обходу диска)."""
    with _lock:
        groups = _groups
    if groups is None:
        return []
    table = _process_table()
    for group in groups.values():
        if group["file_count"]:
            group["running"] = bool(_group_processes(group, table))
    with _lock:
        return _sorted_public(groups)


def _get_group(key: str) -> dict | None:
    with _lock:
        return None if _groups is None else _groups.get(key)


def group_name(key: str) -> str:
    group = _get_group(key)
    return group["name"] if group else key


def clean_group(key: str, action) -> dict:
    """Видаляє вміст знайдених папок кешу групи — лише з action (process_control.UserAction).
    Зайняті файли пропускаються без помилок. Кожна тека пишеться в журнал аудиту."""
    process_control.require(action, f"deleting cache of program {key}")
    audit = get_audit_logger()
    result = {"key": key, "freed_bytes": 0, "deleted_count": 0, "skipped_count": 0, "skipped_reason": None}
    group = _get_group(key)
    if group is None:
        result["skipped_reason"] = t("app_cache.err.rescan")
        return result

    if _group_processes(group, _process_table()):
        result["skipped_reason"] = t("app_cache.err.running")
        return result

    for folder in group["folders"]:
        path = folder["path"]
        if not _safe_dir(path):
            continue
        freed = deleted = skipped = 0
        for file_path, size in list(_walk_files(path, folder["exts"])):
            try:
                os.remove(file_path)
                freed += size
                deleted += 1
            except OSError:
                skipped += 1
        if folder["exts"] is None:
            _remove_empty_dirs(path)
        if deleted or skipped:
            audit.info("Deleted %d files (%d bytes, skipped %d) in %s — program \"%s\", reason: %s",
                       deleted, freed, skipped, path, group["name"], action.reason)
        result["freed_bytes"] += freed
        result["deleted_count"] += deleted
        result["skipped_count"] += skipped
    return result


def _remove_empty_dirs(path: str) -> None:
    """Прибирає порожні підтеки всередині path (саму path залишає)."""
    for entry in _subdirs(path):
        if _is_forbidden(entry.name):
            continue
        _remove_empty_dirs(entry.path)
        try:
            os.rmdir(entry.path)
        except OSError:
            pass


def close_group(key: str, action) -> dict:
    """Закриває всі процеси програми через process_control (лише з UserAction).
    Повертає {"ok", "relaunch", "message"}: relaunch — ("aumid", id) / ("exe", шлях)
    для повторного запуску або None."""
    process_control.require(action, f"closing program {key}")
    group = _get_group(key)
    if group is None:
        return {"ok": False, "relaunch": None, "message": t("app_cache.err.rescan")}

    matched = _group_processes(group, _process_table())
    if not matched:
        return {"ok": True, "relaunch": None, "message": ""}

    pids = {p["pid"] for p in matched}
    roots = [p for p in matched if p["ppid"] not in pids and p["exe_raw"]] or [p for p in matched if p["exe_raw"]]
    main = next((p for p in roots if group["exe"] and p["exe"] == group["exe"].lower()), roots[0] if roots else None)
    relaunch = None
    if main:
        if group.get("aumid") and "\\windowsapps\\" in main["exe"]:
            relaunch = ("aumid", group["aumid"])
        else:
            relaunch = ("exe", main["exe_raw"])

    # Steam коректно завершується сам (зберігає стан завантажень) — спершу -shutdown.
    targets = [(p["pid"], p["create_time"]) for p in matched]
    killed, errors = process_control.terminate_processes(targets, action, graceful_command=group.get("shutdown_args"))
    time.sleep(0.5)  # Windows відпускає дескриптори файлів не миттєво

    if _group_processes(group, _process_table()):
        _log.error("Failed to close program %s: %s", key, "; ".join(errors))
        denied_suffix = t("proc_control.err.no_rights", name="")  # ": немає прав …" — з process_control
        denied = any(e.endswith(denied_suffix) for e in errors)
        return {"ok": False, "relaunch": relaunch,
                "message": t("app_cache.err.close") + (t("app_cache.err.no_rights_suffix") if denied else "")}
    return {"ok": True, "relaunch": relaunch, "message": ""}


def relaunch(target) -> bool:
    """Запускає програму знову. Lagnix працює з правами адміністратора, тому
    запуск іде через explorer.exe — програма стартує зі звичайними правами."""
    if not target:
        return False
    kind, value = target
    try:
        if kind == "aumid":
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{value}"])
        elif is_admin():
            subprocess.Popen(["explorer.exe", value])
        else:
            os.startfile(value)
    except OSError:
        _log.exception("Failed to launch %s", value)
        return False
    return True
