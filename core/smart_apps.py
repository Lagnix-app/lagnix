"""Розумний список фонових програм, які варто закрити перед грою.

Береться той самий знімок процесів, що й на «Моніторі», з групуванням у програми
(Edge з 40 процесів = одна програма). Пропонуються браузери, лаунчери, хмарні
клієнти, месенджери для дзвінків і оновлювачі. НІКОЛИ не пропонуються:
  * Discord, запис/стрім (OBS, ShadowPlay, Medal…), оверлеї;
  * античити (vgc, EasyAntiCheat, BattlEye, FACEIT…);
  * системні процеси і сам PulseFPS.
Лаунчер магазину не пропонується, якщо в цьому магазині знайдено встановлені
ігри — інакше гра, запущена через Steam, лишилась би без нього.

Закриті PulseFPS програми запам'ятовуються (назва + exe), щоб після
вимкнення режиму запропонувати відкрити лише їх.
"""

from __future__ import annotations

import os
import subprocess

from core import monitor as monitor_core
from core import process_control, process_info
from core.logging_setup import get_logger
from core.system_processes import is_hidden, is_protected

_logger = get_logger(__name__)
_CURRENT_PID = os.getpid()

BROWSER, LAUNCHER, CLOUD, CALLS, UPDATER, OTHER = "browser", "launcher", "cloud", "calls", "updater", "other"
CATEGORY_LABELS = {
    BROWSER: "Браузер", LAUNCHER: "Лаунчер", CLOUD: "Хмарний клієнт",
    CALLS: "Месенджер / дзвінки", UPDATER: "Оновлювач", OTHER: "Фонова програма",
}

_BROWSERS = {
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe", "vivaldi.exe",
    "browser.exe", "waterfox.exe", "librewolf.exe", "chromium.exe", "arc.exe",
}
# exe лаунчера -> платформа (ключ збігається з game_scanner)
_LAUNCHERS = {
    "steam.exe": "Steam", "steamwebhelper.exe": "Steam",
    "epicgameslauncher.exe": "Epic",
    "battle.net.exe": "Battle.net",
    "eadesktop.exe": "EA", "origin.exe": "EA", "eabackgroundservice.exe": "EA",
    "ubisoftconnect.exe": "Ubisoft", "upc.exe": "Ubisoft", "uplaywebcore.exe": "Ubisoft",
    "riotclientservices.exe": "Riot", "riotclientux.exe": "Riot",
    "galaxyclient.exe": "GOG", "xboxpcapp.exe": "Xbox",
}
_CLOUD = {
    "onedrive.exe", "dropbox.exe", "googledrivefs.exe", "googledrive.exe", "megasync.exe",
    "icloudservices.exe", "icloudphotos.exe", "yandexdisk2.exe",
}
_CALLS = {"teams.exe", "ms-teams.exe", "msteams.exe", "slack.exe", "skype.exe", "zoom.exe", "skypeapp.exe"}
_UPDATERS = {
    "googleupdate.exe", "microsoftedgeupdate.exe", "adobearm.exe", "adobeipcbroker.exe",
    "creative cloud.exe", "ccxprocess.exe", "cclibrary.exe", "adobe desktop service.exe",
    "jusched.exe", "ituneshelper.exe", "opera_autoupdate.exe",
}
_OTHER = {"widgets.exe", "phoneexperiencehost.exe", "yourphone.exe", "cortana.exe"}

_CATEGORY_OF: dict[str, str] = {}
for _names, _cat in ((_BROWSERS, BROWSER), (_LAUNCHERS, LAUNCHER), (_CLOUD, CLOUD),
                     (_CALLS, CALLS), (_UPDATERS, UPDATER), (_OTHER, OTHER)):
    for _n in _names:
        _CATEGORY_OF[_n] = _cat

# Ніколи не пропонуємо (підрядки в назві exe, нижній регістр): стрім/запис, оверлеї, античити
_NEVER_SUBSTRINGS = (
    "discord", "obs64", "obs32", "obs-", "streamlabs", "slobs", "xsplit", "medal", "outplayed",
    "bandicam", "fraps", "nvcontainer", "nvidia share", "nvsphelper", "nvidia overlay", "nvidia app",
    "nvcplui", "shadowplay", "gamebar", "amdrsserv", "radeonsoftware", "rtss", "msiafterburner",
    "easyanticheat", "battleye", "beservice", "beclient", "faceit", "vgc", "vgtray", "vanguard",
    "xigncode", "gameguard", "nprotect", "punkbuster", "pnkbstr", "eosanticheat", "pulsefps",
)


def is_never_suggested(name: str) -> bool:
    low = name.lower()
    return (
        any(s in low for s in _NEVER_SUBSTRINGS)
        or process_info.kind_for(name) in (process_info.ANTICHEAT, process_info.SYSTEM)
        or is_protected(name) or is_hidden(name)
    )


def category_of(name: str) -> str | None:
    return _CATEGORY_OF.get(name.lower())


def compute_suggestions(excluded: set[str], game_platforms: set[str] = frozenset(),
                        groups: list[dict] | None = None) -> list[dict]:
    """Програми до закриття, за розміром RAM (більші першими). excluded — назви
    exe (нижній регістр), які користувач прибрав зі списку. Кожен елемент:
    key (= назва exe кореня), title, name, exe_path, category, memory_mb,
    count, targets [(pid, create_time)]."""
    if groups is None:
        groups = monitor_core.get_process_groups()
    result = []
    for group in groups:
        root_name = group["name"]
        key = root_name.lower()
        category = _CATEGORY_OF.get(key)
        if category is None or key in excluded or is_never_suggested(root_name):
            continue
        if category == LAUNCHER and _LAUNCHERS.get(key) in game_platforms:
            continue
        members = [
            m for m in group["members"]
            if m["pid"] != _CURRENT_PID and not is_protected(m["name"]) and not is_never_suggested(m["name"])
        ]
        if not members:
            continue
        result.append({
            "key": key,
            "title": group["title"],
            "name": root_name,
            "exe_path": group.get("exe_path"),
            "category": category,
            "memory_mb": sum(m["memory_mb"] for m in members),
            "count": len(members),
            "targets": [(m["pid"], m.get("create_time")) for m in members],
        })
    result.sort(key=lambda a: a["memory_mb"], reverse=True)
    return result


def close_apps(apps: list[dict], action) -> tuple[list[dict], list[str]]:
    """Закриває програми, які користувач щойно підтвердив (action — process_control.UserAction).
    -> (реально закриті у форматі для збереження {title, name, exe_path, memory_mb}, помилки)."""
    closed, errors = [], []
    for app in apps:
        try:
            killed, errs = process_control.terminate_processes(app["targets"], action, timeout=3)
        except Exception as exc:
            _logger.exception("Не вдалося закрити %s", app["name"])
            errors.append(f"{app['title']}: {exc}")
            continue
        errors.extend(f"{app['title']}: {e}" for e in errs)
        if killed:
            closed.append({"title": app["title"], "name": app["name"], "exe_path": app.get("exe_path"),
                           "memory_mb": app["memory_mb"]})
    return closed, errors


def reopen_apps(apps: list[dict]) -> list[str]:
    """Запускає програми знову. Через explorer.exe — так вони стартують із правами
    звичайного користувача, а не адміністратора (PulseFPS завжди elevated).
    -> назви, які не вдалося запустити."""
    failed = []
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for app in apps:
        path = app.get("exe_path")
        if not path or not os.path.isfile(path):
            failed.append(app.get("title") or app.get("name", "?"))
            continue
        try:
            subprocess.Popen(["explorer.exe", path], creationflags=flags)
        except OSError:
            failed.append(app.get("title") or app["name"])
    return failed
