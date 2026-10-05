"""Списки системних процесів Windows.

HIDDEN_PROCESS_NAMES — процеси, які взагалі не показуються в списках
(ядро ОС, не є реальними керованими процесами).

PROTECTED_PROCESS_NAMES — процеси, які показуються, але їх не можна
завершити через інтерфейс (кнопка «Завершити» замінюється позначкою
«системний»), бо це призведе до нестабільності або збою системи.

Використовується вкладками «Монітор» та «Ігровий режим».
"""

from core import anticheat

HIDDEN_PROCESS_NAMES = {
    "system idle process",
    "system",
}

PROTECTED_PROCESS_NAMES = {
    "system",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "lsaiso.exe",
    "svchost.exe",
    "dwm.exe",
    "explorer.exe",
    "memcompression",
    "memory compression",  # так його називає NtQuerySystemInformation
    "msmpeng.exe",
    "nissrv.exe",
    "securityhealthservice.exe",
    "securityhealthsystray.exe",
    "fontdrvhost.exe",
    "sihost.exe",
    "ctfmon.exe",
    "taskhostw.exe",
    "runtimebroker.exe",
    "spoolsv.exe",
    "audiodg.exe",
    "conhost.exe",
    "wlanext.exe",
    "wmiprvse.exe",
    "dllhost.exe",
    "shellexperiencehost.exe",
    "startmenuexperiencehost.exe",
    "searchindexer.exe",
    "searchapp.exe",
    "searchhost.exe",
    "textinputhost.exe",
    "applicationframehost.exe",
    "systemsettings.exe",
    "smartscreen.exe",
}


def is_hidden(name: str) -> bool:
    return bool(name) and name.strip().lower() in HIDDEN_PROCESS_NAMES


def is_protected(name: str) -> bool:
    """Системний процес або процес античита — Lagnix їх не завершує ніколи (core/anticheat.py)."""
    return (bool(name) and name.strip().lower() in PROTECTED_PROCESS_NAMES) or anticheat.is_anticheat_process(name)
