"""Пошук установлених ігор: Steam, Epic, Riot, Battle.net, EA, Ubisoft.

Кожна гра — dict:
  key       — стабільний ключ ("steam:730"), за ним зберігається перемикач автозапуску;
  name      — назва для показу;
  platform  — "Steam" / "Epic" / "Riot" / "Battle.net" / "EA" / "Ubisoft";
  folder    — тека встановлення;
  exe       — головний exe (для іконки) або None;
  exe_names — назви exe у нижньому регістрі, за якими бачимо, що гра запущена;
  kind      — "game" або "other" (Blender, Wallpaper Engine, редактори й інші
              не-ігри: для Steam — за типом програми з appcache/appinfo.vdf,
              для Epic — за категоріями маніфесту, інакше — за назвою).

Джерела: appmanifest_*.acf (Steam), Manifests/*.item (Epic), RiotClientInstalls.json
і Metadata/*.product_settings.yaml (Riot), розділи реєстру Blizzard / EA / Ubisoft
(Battle.net, EA app і Ubisoft Connect не мають простих маніфестів, але
записують теку гри в реєстр). Усе читається тільки з диска/реєстру — жоден
лаунчер не запускається.
"""

from __future__ import annotations

import json
import os
import mmap
import re
import struct
import winreg

from core.installed_programs import (
    _EPIC_MANIFESTS_DIR,
    _find_steam_install_path,
    _parse_steam_library_paths,
    _vdf_field,
)
from core.logging_setup import get_logger

_logger = get_logger(__name__)

# exe, які не є грою: інсталятори, краш-репортери, античити, redist, лаунчери
_SKIP_EXE_RE = re.compile(
    r"unins|uninstall|crash|report|redist|vc_?redist|dxsetup|dotnet|setup|install|updater|"
    r"prereq|easyanticheat|eac|battleye|beservice|helper|webhelper|benchmark|cef|notification|"
    r"config|editor|launcher|bootstrap|ue4|oalinst|dxwebsetup|handler|diagnos|support|console|"
    r"python|symbol|superluminal|profil|studiomdl|dmxedit|elementviewer|faceposer|makescenes|qc_|"
    r"statsreset|hlds|hltv|srcds|server|sdk|tool|node|java|cmd|ffmpeg|7z|start_protected|_be\.|_zk\.|dmxconvert|vbsp|vpk|vtf|bsp|mksheet|height2|normal2|pfm2|hlmv",
    re.IGNORECASE,
)
_SKIP_NAMES = ("steamworks", "redistributable", "steam linux runtime", "proton", "soundtrack")
# не-ігри за назвою — запасний шлях, коли тип програми невідомий
_OTHER_NAME_RE = re.compile(
    r"\b(tools?|sdk|editor|dedicated server|wallpaper engine|steamvr|blender|benchmark|"
    r"soundtrack|artbook|modding kit|mod tools|authoring)\b",
    re.IGNORECASE,
)
_SCAN_DEPTH = 3
_SCAN_LIMIT = 400  # найбільше елементів теки, які переглядаємо на одну гру


def _find_exes(folder: str) -> list[str]:
    """Шляхи до exe (ігрових) у теці гри на глибину _SCAN_DEPTH; найбільші — першими."""
    found: list[tuple[int, str]] = []
    seen = 0
    base_depth = folder.rstrip("\\/").count(os.sep)
    for root, dirs, files in os.walk(folder):
        if root.count(os.sep) - base_depth >= _SCAN_DEPTH:
            dirs[:] = []
        for file in files:
            seen += 1
            if file.lower().endswith(".exe") and not _SKIP_EXE_RE.search(file):
                path = os.path.join(root, file)
                try:
                    found.append((os.path.getsize(path), path))
                except OSError:
                    continue
        if seen > _SCAN_LIMIT:
            break
    found.sort(reverse=True)
    return [path for _size, path in found]


def _tokens(text: str) -> list[str]:
    return [w for w in re.split(r"\W+", text.lower()) if len(w) > 2]


def _make_game(key: str, name: str, platform: str, folder: str, exe: str | None = None,
               kind: str | None = None) -> dict | None:
    if not folder or not os.path.isdir(folder):
        return None
    exes = _find_exes(folder)  # найбільші першими
    if exe and os.path.isfile(exe):
        exes = [exe] + [p for p in exes if p != exe]
    if not exes:
        return None
    # екземпляри, назва яких схожа на назву гри чи теки, — головні; далі найбільші
    words = _tokens(name) + _tokens(os.path.basename(folder.rstrip("\\/")))

    def matches(path: str) -> bool:
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        stem_flat = re.sub(r"\W+", "", stem)
        return any(w in stem_flat for w in words)
    root = os.path.normcase(os.path.normpath(folder))
    ordered = sorted(exes, key=lambda p: (not matches(p), os.path.normcase(os.path.dirname(p)) != root))
    main = exe if exe and os.path.isfile(exe) else ordered[0]
    chosen = [p for p in ordered if matches(p) or os.path.normcase(os.path.dirname(p)) == root][:4] or ordered[:2]
    return {
        "key": key, "name": name, "platform": platform, "folder": folder, "exe": main,
        "exe_names": sorted({os.path.basename(p).lower() for p in chosen} | {os.path.basename(main).lower()}),
        "kind": kind or ("other" if _OTHER_NAME_RE.search(name) else "game"),
    }


# ------------------------------------------------------------------- Steam

# appinfo.vdf — двійковий кеш метаданих Steam; тип програми — appinfo/common/type
# ("Game", "Tool", "Application", "Music", "Demo"…). Формати: 0x27 (старий),
# 0x28 (+SHA-1 двійкового блоку), 0x29 (ключі — індекси в таблиці рядків у кінці).
_APPINFO_MAGICS = {0x07564427: 27, 0x07564428: 28, 0x07564429: 29}
_GAME_TYPES = {"game", "demo", "mod", "beta"}


class _BinVdf:
    """Мінімальний читач двійкового KeyValues: лише те, що треба для common/type."""

    def __init__(self, data, pos: int, strings: list[str] | None):
        self.data, self.pos, self.strings = data, pos, strings

    def _cstr(self) -> str:
        end = self.data.find(b"\x00", self.pos)
        text = self.data[self.pos:end].decode("utf-8", "replace")
        self.pos = end + 1
        return text

    def _key(self) -> str:
        if self.strings is None:
            return self._cstr()
        (index,) = struct.unpack_from("<I", self.data, self.pos)
        self.pos += 4
        return self.strings[index] if index < len(self.strings) else ""

    def read_map(self, depth: int = 0) -> dict:
        result = {}
        while True:
            kind = self.data[self.pos]
            self.pos += 1
            if kind in (0x08, 0x0B):  # кінець мапи
                return result
            key = self._key()
            if kind == 0x00:
                if depth > 32:
                    raise ValueError("appinfo: надто глибока вкладеність")
                result[key] = self.read_map(depth + 1)
            elif kind == 0x01:
                result[key] = self._cstr()
            elif kind in (0x02, 0x03, 0x04, 0x06):
                self.pos += 4
            elif kind in (0x07, 0x0A):
                self.pos += 8
            elif kind == 0x05:  # UTF-16 рядок до подвійного нуля
                while self.data[self.pos:self.pos + 2] != b"\x00\x00":
                    self.pos += 2
                self.pos += 2
            else:
                raise ValueError(f"appinfo: невідомий тип {kind:#x}")


def _steam_app_types(steam_path: str, appids: set[int]) -> dict[int, str]:
    """appid -> тип програми (нижній регістр) з appcache/appinfo.vdf; {} при будь-якій помилці."""
    path = os.path.join(steam_path, "appcache", "appinfo.vdf")
    types: dict[int, str] = {}
    try:
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            magic, _universe = struct.unpack_from("<II", data, 0)
            version = _APPINFO_MAGICS.get(magic)
            if version is None:
                return types
            pos, strings = 8, None
            if version >= 29:
                (table_at,) = struct.unpack_from("<q", data, 8)
                pos = 16
                (count,) = struct.unpack_from("<I", data, table_at)
                strings = data[table_at + 4:].split(b"\x00", count)[:count]
                strings = [s.decode("utf-8", "replace") for s in strings]
            # заголовок запису після size: state, last_updated, token, sha1, change, [sha1 блоку]
            header = 4 + 4 + 8 + 20 + 4 + (20 if version >= 28 else 0)
            while len(types) < len(appids):
                appid, size = struct.unpack_from("<II", data, pos)
                if appid == 0:
                    break
                body = pos + 8
                if appid in appids:
                    try:
                        info = _BinVdf(data, body + header, strings).read_map()
                        kind = info.get("appinfo", {}).get("common", {}).get("type")
                        if kind:
                            types[appid] = kind.lower()
                    except (ValueError, IndexError, struct.error):
                        pass
                pos = body + size
    except (OSError, ValueError, struct.error):
        _logger.warning("Не вдалося прочитати типи програм Steam (%s)", path, exc_info=True)
    return types


def _scan_steam() -> list[dict]:
    steam_path = _find_steam_install_path()
    if not steam_path:
        return []
    found = []
    for library in _parse_steam_library_paths(steam_path):
        steamapps = os.path.join(library, "steamapps")
        try:
            entries = os.listdir(steamapps)
        except OSError:
            continue
        for entry in entries:
            if not (entry.startswith("appmanifest_") and entry.endswith(".acf")):
                continue
            try:
                with open(os.path.join(steamapps, entry), "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except OSError:
                continue
            appid, name, installdir = (_vdf_field(content, k) for k in ("appid", "name", "installdir"))
            if not (appid and appid.isdigit() and name and installdir) or any(s in name.lower() for s in _SKIP_NAMES):
                continue
            found.append((int(appid), name, os.path.join(steamapps, "common", installdir)))
    types = _steam_app_types(steam_path, {appid for appid, _n, _f in found})
    games = []
    for appid, name, folder in found:
        app_type = types.get(appid)
        kind = None if app_type is None else ("game" if app_type in _GAME_TYPES else "other")
        game = _make_game(f"steam:{appid}", name, "Steam", folder, kind=kind)
        if game:
            games.append(game)
    return games


# -------------------------------------------------------------------- Epic

def _scan_epic() -> list[dict]:
    games = []
    try:
        names = os.listdir(_EPIC_MANIFESTS_DIR)
    except OSError:
        return games
    for file in names:
        if not file.endswith(".item"):
            continue
        try:
            with open(os.path.join(_EPIC_MANIFESTS_DIR, file), "r", encoding="utf-8", errors="ignore") as f:
                item = json.load(f)
        except (OSError, ValueError):
            continue
        folder = item.get("InstallLocation") or ""
        name = item.get("DisplayName") or item.get("AppName") or ""
        launch = item.get("LaunchExecutable") or ""
        if not (folder and name) or item.get("bIsIncompleteInstall"):
            continue
        exe = os.path.join(folder, launch) if launch else None
        categories = [str(c).lower() for c in item.get("AppCategories") or []]
        kind = ("game" if "games" in categories else "other") if categories else None
        game = _make_game(f"epic:{item.get('AppName') or name}", name, "Epic", folder, exe, kind=kind)
        if game:
            games.append(game)
    return games


# -------------------------------------------------------------------- Riot

_RIOT_NAMES = {
    "valorant": "VALORANT", "league_of_legends": "League of Legends",
    "teamfighttactics": "Teamfight Tactics", "bacon": "Legends of Runeterra", "lion": "2XKO",
}
_RIOT_DATA = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Riot Games")


def _scan_riot() -> list[dict]:
    games = []
    metadata = os.path.join(_RIOT_DATA, "Metadata")
    try:
        products = os.listdir(metadata)
    except OSError:
        return games
    for product in products:
        if not product.endswith(".live"):  # .pbe/.game_patch — не окремі ігри
            continue
        yaml_path = os.path.join(metadata, product, f"{product}.product_settings.yaml")
        try:
            with open(yaml_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError:
            continue
        match = re.search(r'product_install_full_path:\s*"?([^"\r\n]+?)"?\s*$', content, re.MULTILINE)
        if not match:
            continue
        folder = os.path.normpath(match.group(1).strip())
        slug = product[:-len(".live")]
        game = _make_game(f"riot:{slug}", _RIOT_NAMES.get(slug, slug.replace("_", " ").title()), "Riot", folder)
        if game:
            games.append(game)
    return games


# ------------------------------------------- Battle.net / EA / Ubisoft (реєстр)

_UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"


def _iter_uninstall():
    """(назва ключа, DisplayName, Publisher, InstallLocation) усіх програм із реєстру."""
    for hive, view in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
                       (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                       (winreg.HKEY_CURRENT_USER, 0)):
        try:
            root = winreg.OpenKey(hive, _UNINSTALL, 0, winreg.KEY_READ | view)
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
                        values = []
                        for field in ("DisplayName", "Publisher", "InstallLocation"):
                            try:
                                values.append(str(winreg.QueryValueEx(key, field)[0]))
                            except OSError:
                                values.append("")
                        yield (sub, *values)
                except OSError:
                    continue


def _scan_publishers() -> list[dict]:
    """Battle.net (Blizzard) і EA — за видавцем із запису про видалення."""
    prefixes = (("blizzard entertainment", "Battle.net"), ("electronic arts", "EA"))
    games = []
    for sub, name, publisher, location in _iter_uninstall():
        platform = next((p for prefix, p in prefixes if publisher.lower().startswith(prefix)), None)
        if not platform or not name or not location or name.lower().startswith(("battle.net", "ea app", "origin", "ea desktop")):
            continue
        game = _make_game(f"{platform.lower()}:{sub}", name, platform, location)
        if game:
            games.append(game)
    return games


def _scan_ubisoft() -> list[dict]:
    games = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs")
    except OSError:
        return games
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
                    folder = str(winreg.QueryValueEx(key, "InstallDir")[0])
            except OSError:
                continue
            folder = os.path.normpath(folder)
            game = _make_game(f"ubisoft:{sub}", os.path.basename(folder.rstrip("\\/")), "Ubisoft", folder)
            if game:
                games.append(game)
    return games


def scan_games() -> list[dict]:
    """Усі знайдені ігри, за назвою. Збій одного магазину не заважає іншим."""
    games: list[dict] = []
    for scanner in (_scan_steam, _scan_epic, _scan_riot, _scan_publishers, _scan_ubisoft):
        try:
            games.extend(scanner())
        except Exception:
            _logger.exception("Помилка пошуку ігор (%s)", scanner.__name__)
    unique = {g["key"]: g for g in games}
    return sorted(unique.values(), key=lambda g: g["name"].lower())
