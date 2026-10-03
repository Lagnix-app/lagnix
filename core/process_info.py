"""Пояснення до процесів для «Монітора» та «Ігрового режиму».

KNOWN_PROCESSES: назва exe (у нижньому регістрі) -> пояснення простими
словами, порада й вид процесу:
  * "system"    — частина Windows, закривати не можна / не варто;
  * "anticheat" — античит гри: не закривати перед грою й під час неї;
  * "safe"      — звичайна програма, закрити безпечно (звільнить ресурси);
  * None        — нейтральний (закривати можна, але є нюанси — див. пораду).

Для невідомих процесів підказка показує шлях до exe й видавця з
метаданих файлу (VERSIONINFO), тож користувач може сам зрозуміти, що це.

Тексти — ключі перекладів (locales/*.json, core/i18n.py): info_for() і
tooltip_text() повертають їх поточною мовою інтерфейсу.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

import psutil

from core.system_processes import is_protected
from core.i18n import TDict, t

SYSTEM, ANTICHEAT, SAFE = "system", "anticheat", "safe"

BADGES = TDict({
    SYSTEM: "proc.badge.system",
    ANTICHEAT: "proc.badge.anticheat",
    SAFE: "proc.badge.safe",
})

ANTICHEAT_WARNING = "proc.anticheat_warning"  # ключ перекладу; текст — anticheat_warning()


def anticheat_warning() -> str:
    return t(ANTICHEAT_WARNING)


def badge_text(kind: str) -> str:
    return BADGES[kind]


# desc/tip — ключі перекладів; info_for() повертає вже перекладений текст
def _p(kind, desc: str, tip: str) -> dict:
    return {"kind": kind, "desc": desc, "tip": tip}


KNOWN_PROCESSES: dict[str, dict] = {
    # ------------------------------------------------------------ Windows
    "memory compression": _p(
        SYSTEM,
        "proc.memory_compression.desc",
        "proc.memory_compression.tip",
    ),
    "system": _p(
        SYSTEM,
        "proc.system.desc",
        "proc.system.tip",
    ),
    "registry": _p(
        SYSTEM,
        "proc.registry.desc",
        "proc.registry.tip",
    ),
    "svchost.exe": _p(
        SYSTEM,
        "proc.svchost.desc",
        "proc.svchost.tip",
    ),
    "dwm.exe": _p(
        SYSTEM,
        "proc.dwm.desc",
        "proc.dwm.tip",
    ),
    "explorer.exe": _p(
        SYSTEM,
        "proc.explorer.desc",
        "proc.explorer.tip",
    ),
    "csrss.exe": _p(
        SYSTEM,
        "proc.csrss.desc",
        "proc.csrss.tip",
    ),
    "lsass.exe": _p(
        SYSTEM,
        "proc.lsass.desc",
        "proc.lsass.tip",
    ),
    "services.exe": _p(
        SYSTEM,
        "proc.services.desc",
        "proc.services.tip",
    ),
    "wininit.exe": _p(
        SYSTEM,
        "proc.wininit.desc",
        "proc.wininit.tip",
    ),
    "winlogon.exe": _p(
        SYSTEM,
        "proc.winlogon.desc",
        "proc.winlogon.tip",
    ),
    "smss.exe": _p(
        SYSTEM,
        "proc.smss.desc",
        "proc.smss.tip",
    ),
    "msmpeng.exe": _p(
        SYSTEM,
        "proc.msmpeng.desc",
        "proc.msmpeng.tip",
    ),
    "nissrv.exe": _p(
        SYSTEM,
        "proc.nissrv.desc",
        "proc.nissrv.tip",
    ),
    "securityhealthservice.exe": _p(
        SYSTEM,
        "proc.securityhealthservice.desc",
        "proc.securityhealthservice.tip",
    ),
    "audiodg.exe": _p(
        SYSTEM,
        "proc.audiodg.desc",
        "proc.audiodg.tip",
    ),
    "searchindexer.exe": _p(
        SYSTEM,
        "proc.searchindexer.desc",
        "proc.searchindexer.tip",
    ),
    "searchhost.exe": _p(
        SYSTEM,
        "proc.searchhost.desc",
        "proc.searchhost.tip",
    ),
    "runtimebroker.exe": _p(
        SYSTEM,
        "proc.runtimebroker.desc",
        "proc.runtimebroker.tip",
    ),
    "wmiprvse.exe": _p(
        SYSTEM,
        "proc.wmiprvse.desc",
        "proc.wmiprvse.tip",
    ),
    "ctfmon.exe": _p(
        SYSTEM,
        "proc.ctfmon.desc",
        "proc.ctfmon.tip",
    ),
    "fontdrvhost.exe": _p(
        SYSTEM,
        "proc.fontdrvhost.desc",
        "proc.securityhealthservice.tip",
    ),
    "sihost.exe": _p(
        SYSTEM,
        "proc.sihost.desc",
        "proc.sihost.tip",
    ),
    "spoolsv.exe": _p(
        SYSTEM,
        "proc.spoolsv.desc",
        "proc.spoolsv.tip",
    ),
    "taskhostw.exe": _p(
        SYSTEM,
        "proc.taskhostw.desc",
        "proc.taskhostw.tip",
    ),
    "conhost.exe": _p(
        SYSTEM,
        "proc.conhost.desc",
        "proc.conhost.tip",
    ),
    "dllhost.exe": _p(
        SYSTEM,
        "proc.dllhost.desc",
        "proc.taskhostw.tip",
    ),
    "startmenuexperiencehost.exe": _p(
        SYSTEM,
        "proc.startmenuexperiencehost.desc",
        "proc.startmenuexperiencehost.tip",
    ),
    "shellexperiencehost.exe": _p(
        SYSTEM,
        "proc.shellexperiencehost.desc",
        "proc.taskhostw.tip",
    ),
    "textinputhost.exe": _p(
        SYSTEM,
        "proc.textinputhost.desc",
        "proc.taskhostw.tip",
    ),
    "applicationframehost.exe": _p(
        SYSTEM,
        "proc.applicationframehost.desc",
        "proc.taskhostw.tip",
    ),
    "smartscreen.exe": _p(
        SYSTEM,
        "proc.smartscreen.desc",
        "proc.smartscreen.tip",
    ),
    "lsaiso.exe": _p(
        SYSTEM,
        "proc.lsaiso.desc",
        "proc.lsaiso.tip",
    ),
    # ---------------------------------------------------------- античити
    "vgc.exe": _p(
        ANTICHEAT,
        "proc.vgc.desc",
        "proc.vgc.tip",
    ),
    "vgtray.exe": _p(
        ANTICHEAT,
        "proc.vgtray.desc",
        ANTICHEAT_WARNING,
    ),
    "easyanticheat.exe": _p(
        ANTICHEAT,
        "proc.easyanticheat.desc",
        ANTICHEAT_WARNING,
    ),
    "easyanticheat_eos.exe": _p(
        ANTICHEAT,
        "proc.easyanticheat_eos.desc",
        ANTICHEAT_WARNING,
    ),
    "beservice.exe": _p(
        ANTICHEAT,
        "proc.beservice.desc",
        ANTICHEAT_WARNING,
    ),
    "faceit.exe": _p(
        ANTICHEAT,
        "proc.faceit.desc",
        ANTICHEAT_WARNING,
    ),
    # --------------------------------------------------------- програми
    "chrome.exe": _p(
        SAFE,
        "proc.chrome.desc",
        "proc.chrome.tip",
    ),
    "msedge.exe": _p(
        SAFE,
        "proc.msedge.desc",
        "proc.msedge.tip",
    ),
    "msedgewebview2.exe": _p(
        None,
        "proc.msedgewebview2.desc",
        "proc.msedgewebview2.tip",
    ),
    "firefox.exe": _p(
        SAFE,
        "proc.firefox.desc",
        "proc.firefox.tip",
    ),
    "discord.exe": _p(
        SAFE,
        "proc.discord.desc",
        "proc.discord.tip",
    ),
    "steam.exe": _p(
        SAFE,
        "proc.steam.desc",
        "proc.steam.tip",
    ),
    "steamwebhelper.exe": _p(
        None,
        "proc.steamwebhelper.desc",
        "proc.steamwebhelper.tip",
    ),
    "epicgameslauncher.exe": _p(
        SAFE,
        "proc.epicgameslauncher.desc",
        "proc.epicgameslauncher.tip",
    ),
    "riotclientservices.exe": _p(
        SAFE,
        "proc.riotclientservices.desc",
        "proc.riotclientservices.tip",
    ),
    "telegram.exe": _p(
        SAFE,
        "proc.telegram.desc",
        "proc.telegram.tip",
    ),
    "onedrive.exe": _p(
        SAFE,
        "proc.onedrive.desc",
        "proc.onedrive.tip",
    ),
    "teams.exe": _p(
        SAFE,
        "proc.teams.desc",
        "proc.teams.tip",
    ),
    "ms-teams.exe": _p(
        SAFE,
        "proc.ms-teams.desc",
        "proc.teams.tip",
    ),
    "spotify.exe": _p(
        SAFE,
        "proc.spotify.desc",
        "proc.spotify.tip",
    ),
    "nvcontainer.exe": _p(
        None,
        "proc.nvcontainer.desc",
        "proc.nvcontainer.tip",
    ),
    "nvidia overlay.exe": _p(
        SAFE,
        "proc.nvidia_overlay.desc",
        "proc.nvidia_overlay.tip",
    ),
    "nvdisplay.container.exe": _p(
        None,
        "proc.nvdisplay_container.desc",
        "proc.nvdisplay_container.tip",
    ),
    "lghub.exe": _p(
        SAFE,
        "proc.lghub.desc",
        "proc.lghub.tip",
    ),
    "lghub_agent.exe": _p(
        None,
        "proc.lghub_agent.desc",
        "proc.lghub_agent.tip",
    ),
    "claude.exe": _p(
        SAFE,
        "proc.claude.desc",
        "proc.claude.tip",
    ),
    "python.exe": _p(
        None,
        "proc.python.desc",
        "proc.python.tip",
    ),
    "pythonw.exe": _p(
        None,
        "proc.pythonw.desc",
        "proc.python.tip",
    ),
}


def info_for(name: str) -> dict | None:
    """{"kind", "desc", "tip"} — пояснення поточною мовою (None — процес невідомий)."""
    info = KNOWN_PROCESSES.get((name or "").strip().lower())
    if info is None:
        return None
    return {"kind": info["kind"], "desc": t(info["desc"]), "tip": t(info["tip"])}


def kind_for(name: str) -> str | None:
    """Вид процесу для позначки: system / anticheat / safe / None."""
    info = info_for(name)
    if info is not None and info["kind"]:
        return info["kind"]
    return SYSTEM if is_protected(name) else None


def is_anticheat(name: str) -> bool:
    return kind_for(name) == ANTICHEAT


# ------------------------------------------------ невідомі: шлях і видавець

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_details_cache: dict = {}

if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def exe_path(pid: int) -> str | None:
    """Повний шлях до exe процесу (права «обмеженого запиту» є майже для всіх)."""
    if sys.platform == "win32":
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            try:
                size = wintypes.DWORD(32768)
                buf = ctypes.create_unicode_buffer(size.value)
                if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    return buf.value
            finally:
                _kernel32.CloseHandle(handle)
    try:
        return psutil.Process(pid).exe() or None
    except (psutil.Error, OSError):
        return None


def _publisher(path: str) -> str | None:
    if sys.platform != "win32":
        return None
    try:
        from core.autostart import read_version_field
    except ImportError:
        return None
    return read_version_field(path, "CompanyName")


def unknown_details(pid: int | None, name: str) -> str:
    """«Шлях: …\\nВидавець: …» для процесу без пояснення (кешується за pid+назвою)."""
    key = (pid, name)
    found = _details_cache.get(key)
    if found is None:
        path = exe_path(pid) if pid else None
        found = (path, _publisher(path) if path else None)
        if len(_details_cache) > 500:
            _details_cache.clear()
        _details_cache[key] = found
    path, publisher = found
    return t("proc.details", path=path or t("proc.path_unavailable"), publisher=publisher or t("proc.publisher_unknown"))


def tooltip_text(name: str, pid: int | None = None) -> str:
    """Текст підказки: пояснення + порада, або шлях і видавець для невідомих."""
    info = info_for(name)
    if info is not None:
        return t("proc.tooltip_known", name=name, desc=info['desc'], tip=info['tip'])
    details = unknown_details(pid, name)
    if is_protected(name):
        return t("proc.tooltip_system", name=name, details=details)
    return t("proc.tooltip_unknown", name=name, details=details)
