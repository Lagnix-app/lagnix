"""Логіка вкладки «Твіки реєстру»: набір фіксованих ігрових твіків реєстру.

Кожен твік — це перемикач "on/off" над одним чи кількома значеннями реєстру.
Стан перемикача завжди читається наживо з реєстру (не кешується), тож він
відображає реальний стан системи, навіть якщо його змінили ззовні.

Перед першою зміною будь-якого твіка (`registry_tweaks_backup_done` у
config.json ще не встановлено): за наявності прав — точка відновлення
Windows (`Checkpoint-Computer`), і повний бекап усіх ключів, які може
чіпати ця вкладка, у backups/ як .reg-файли з датою. Це робиться один раз.

Окремо для кожного твіка — при першій його зміні — початковий стан
зберігається в `registry_tweaks_initial_state` (config.json), звідки його
відновлює кнопка «Повернути все як було».
"""

from __future__ import annotations

import os
import subprocess
import winreg
from dataclasses import dataclass, field
from datetime import datetime

from core.admin import is_admin
from core.logging_setup import get_logger
from core.settings import load_settings, update_setting

_logger = get_logger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BACKUPS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups")

RISK_SAFE = "safe"
RISK_CAUTION = "caution"

_HIVE_NAMES = {
    winreg.HKEY_CURRENT_USER: "HKCU",
    winreg.HKEY_LOCAL_MACHINE: "HKLM",
}


@dataclass(frozen=True)
class RegEntry:
    hive: int
    subkey: str
    name: str
    vtype: str  # "dword" | "sz"
    on_value: object
    off_value: object


@dataclass(frozen=True)
class Tweak:
    id: str
    title: str
    description: str
    risk: str
    entries: tuple[RegEntry, ...]
    requires_reboot: bool = False
    requires_logoff: bool = False
    # Яке значення перемикача показувати, якщо значення реєстру відсутнє
    # (типовий стан Windows "з коробки" для цього твіка).
    missing_state: bool = False


TWEAKS: tuple[Tweak, ...] = (
    Tweak(
        id="game_dvr",
        title="Xbox Game DVR / фоновий запис",
        description=(
            "Вимикає фоновий запис геймплею через Xbox Game Bar — він може забирати "
            "ресурси процесора й диска під час гри."
        ),
        risk=RISK_SAFE,
        entries=(
            RegEntry(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore", "GameDVR_Enabled", "dword", 0, 1),
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\GameDVR",
                "AppCaptureEnabled", "dword", 0, 1,
            ),
        ),
    ),
    Tweak(
        id="mouse_accel",
        title="Прискорення миші (Enhance pointer precision)",
        description=(
            "Прибирає прискорення курсора, щоб рух миші був однаково передбачуваним "
            "на будь-якій швидкості — важливо для точності прицілювання в іграх."
        ),
        risk=RISK_SAFE,
        entries=(
            RegEntry(winreg.HKEY_CURRENT_USER, r"Control Panel\Mouse", "MouseSpeed", "sz", "0", "1"),
            RegEntry(winreg.HKEY_CURRENT_USER, r"Control Panel\Mouse", "MouseThreshold1", "sz", "0", "6"),
            RegEntry(winreg.HKEY_CURRENT_USER, r"Control Panel\Mouse", "MouseThreshold2", "sz", "0", "10"),
        ),
        requires_logoff=True,
    ),
    Tweak(
        id="game_mode",
        title="Game Mode Windows",
        description=(
            "Вбудований режим Windows, який надає грі пріоритет над фоновими "
            "процесами й оновленнями під час запуску."
        ),
        risk=RISK_SAFE,
        entries=(
            RegEntry(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\GameBar",
                "AutoGameModeEnabled", "dword", 1, 0,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\GameBar",
                "AllowAutoGameMode", "dword", 1, 0,
            ),
        ),
        missing_state=True,
    ),
    Tweak(
        id="tips_ads",
        title="Поради й реклама в Пуск та на екрані блокування",
        description=(
            "Прибирає рекламні плитки, підказки й пропозиції застосунків у меню "
            "Пуск та на екрані блокування."
        ),
        risk=RISK_SAFE,
        entries=(
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
                "SubscribedContent-338388Enabled", "dword", 0, 1,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
                "SubscribedContent-338389Enabled", "dword", 0, 1,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
                "RotatingLockScreenOverlayEnabled", "dword", 0, 1,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
                "SoftLandingEnabled", "dword", 0, 1,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
                "SystemPaneSuggestionsEnabled", "dword", 0, 1,
            ),
        ),
        requires_logoff=True,
    ),
    Tweak(
        id="sticky_keys",
        title="Гаряча клавіша залипання клавіш (5× Shift)",
        description=(
            "Вимикає випадкову появу вікна «Залипання клавіш» при швидкому "
            "багаторазовому натисканні Shift під час гри."
        ),
        risk=RISK_SAFE,
        entries=(
            RegEntry(
                winreg.HKEY_CURRENT_USER, r"Control Panel\Accessibility\StickyKeys",
                "Flags", "sz", "506", "510",
            ),
        ),
    ),
    Tweak(
        id="visual_effects",
        title="Візуальні ефекти на продуктивність",
        description=(
            "Вимикає анімації й прикраси інтерфейсу Windows (тіні, прозорість, "
            "плавні переходи) заради швидкодії системи."
        ),
        risk=RISK_SAFE,
        entries=(
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects",
                "VisualFXSetting", "dword", 2, 0,
            ),
            RegEntry(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", "DragFullWindows", "sz", "0", "1"),
            RegEntry(
                winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop\WindowMetrics",
                "MinAnimate", "sz", "0", "1",
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "TaskbarAnimations", "dword", 0, 1,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "ListviewAlphaSelect", "dword", 0, 1,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "ListviewShadow", "dword", 0, 1,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM",
                "EnableAeroPeek", "dword", 0, 1,
            ),
        ),
        requires_logoff=True,
    ),
    Tweak(
        id="background_apps",
        title="Фонові застосунки",
        description=(
            "Забороняє застосункам з Microsoft Store працювати у фоні. Може "
            "вплинути на сповіщення деяких програм (пошта, месенджери)."
        ),
        risk=RISK_CAUTION,
        entries=(
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications",
                "GlobalUserDisabled", "dword", 1, 0,
            ),
        ),
        requires_logoff=True,
    ),
    Tweak(
        id="gpu_scheduling",
        title="Апаратне планування GPU",
        description=(
            "Передає керування чергою кадрів відеокарті замість CPU. На частині "
            "систем підвищує продуктивність, на інших може дати нестабільність — "
            "залежить від драйвера відеокарти."
        ),
        risk=RISK_CAUTION,
        entries=(
            RegEntry(
                winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
                "HwSchMode", "dword", 2, 1,
            ),
        ),
        requires_reboot=True,
    ),
    Tweak(
        id="telemetry",
        title="Телеметрія Windows",
        description=(
            "Знижує обсяг діагностичних даних, які Windows надсилає Microsoft, до "
            "мінімального рівня. На Windows Home/Pro може не вимкнути збір даних "
            "повністю."
        ),
        risk=RISK_CAUTION,
        entries=(
            RegEntry(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\DataCollection",
                "AllowTelemetry", "dword", 0, 1,
            ),
        ),
    ),
)

_TWEAKS_BY_ID = {t.id: t for t in TWEAKS}


def get_tweak(tweak_id: str) -> Tweak | None:
    return _TWEAKS_BY_ID.get(tweak_id)


# --------------------------------------------------------------- реєстр io

def _read_value(hive: int, subkey: str, name: str):
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
            value, _vtype = winreg.QueryValueEx(key, name)
            return value
    except OSError:
        return None


def _write_value(entry: RegEntry, value) -> bool:
    vtype = winreg.REG_DWORD if entry.vtype == "dword" else winreg.REG_SZ
    try:
        with winreg.CreateKeyEx(entry.hive, entry.subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, entry.name, 0, vtype, value)
        return True
    except OSError as exc:
        _logger.error("Не вдалося записати %s\\%s: %s", entry.subkey, entry.name, exc)
        return False


def entry_requires_admin(entry: RegEntry) -> bool:
    return entry.hive == winreg.HKEY_LOCAL_MACHINE


def tweak_requires_admin(tweak: Tweak) -> bool:
    return any(entry_requires_admin(e) for e in tweak.entries)


def get_state(tweak: Tweak) -> bool:
    """Реальний поточний стан твіка: True лише якщо УСІ його значення реєстру
    відповідають "увімкненому" стану."""
    for entry in tweak.entries:
        value = _read_value(entry.hive, entry.subkey, entry.name)
        if value is None:
            if not tweak.missing_state:
                return False
            continue
        if value != entry.on_value:
            return False
    return True


# ------------------------------------------------------------------ backup

def _create_restore_point() -> bool:
    if not is_admin():
        return False
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Checkpoint-Computer -Description 'PulseFPS: твіки реєстру' "
                "-RestorePointType 'MODIFY_SETTINGS'",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, creationflags=_NO_WINDOW,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError) as exc:
        _logger.error("Не вдалося створити точку відновлення: %s", exc)
        return False


def _export_key_backup(hive_name: str, subkey: str, dest_path: str) -> bool:
    try:
        result = subprocess.run(
            ["reg", "export", f"{hive_name}\\{subkey}", dest_path, "/y"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, creationflags=_NO_WINDOW,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError) as exc:
        _logger.error("Не вдалося експортувати %s\\%s: %s", hive_name, subkey, exc)
        return False


def _run_full_backup() -> None:
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    _create_restore_point()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    seen: set[tuple[int, str]] = set()
    for tweak in TWEAKS:
        for entry in tweak.entries:
            key = (entry.hive, entry.subkey)
            if key in seen:
                continue
            seen.add(key)
            hive_name = _HIVE_NAMES[entry.hive]
            safe_subkey = entry.subkey.replace("\\", "_").replace(" ", "_")
            filename = f"{stamp}_{hive_name}_{safe_subkey}.reg"
            _export_key_backup(hive_name, entry.subkey, os.path.join(BACKUPS_DIR, filename))


def _ensure_backup_for(tweak: Tweak) -> None:
    settings = load_settings()

    initial_state = dict(settings.get("registry_tweaks_initial_state", {}))
    if tweak.id not in initial_state:
        initial_state[tweak.id] = get_state(tweak)
        update_setting("registry_tweaks_initial_state", initial_state)

    if not settings.get("registry_tweaks_backup_done", False):
        _run_full_backup()
        update_setting("registry_tweaks_backup_done", True)


def has_initial_state() -> bool:
    return bool(load_settings().get("registry_tweaks_initial_state"))


# ------------------------------------------------------------------ apply

def set_tweak(tweak: Tweak, enabled: bool) -> tuple[bool, str]:
    if tweak_requires_admin(tweak) and not is_admin():
        return False, "Потрібні права адміністратора"

    _ensure_backup_for(tweak)

    for entry in tweak.entries:
        value = entry.on_value if enabled else entry.off_value
        if not _write_value(entry, value):
            return False, f"Не вдалося змінити значення «{entry.name}»"
    return True, ""


def get_recommended_tweaks() -> list[Tweak]:
    return [t for t in TWEAKS if t.risk == RISK_SAFE]


def apply_recommended() -> list[tuple[Tweak, bool, str]]:
    results = []
    for tweak in get_recommended_tweaks():
        if get_state(tweak):
            continue
        success, error = set_tweak(tweak, True)
        results.append((tweak, success, error))
    return results


def restore_initial_state() -> list[tuple[Tweak, bool, str]]:
    settings = load_settings()
    initial_state = settings.get("registry_tweaks_initial_state", {})

    results = []
    for tweak in TWEAKS:
        if tweak.id not in initial_state:
            continue
        target = bool(initial_state[tweak.id])
        if get_state(tweak) == target:
            continue
        success, error = set_tweak(tweak, target)
        results.append((tweak, success, error))
    return results
