"""Логіка вкладки «Твіки реєстру»: набір фіксованих ігрових і системних твіків.

Кожен твік — це перемикач "on/off" над одним чи кількома значеннями реєстру
(плюс, для деяких, дія через системну утиліту: powercfg, sc). Стан перемикача
завжди читається наживо з реєстру (не кешується), тож він відображає реальний
стан системи, навіть якщо його змінили ззовні.

Кожен твік має дві позначки:
- РИЗИК: зелений «безпечно» / жовтий «на свій розсуд» / червоний «ризиковано».
  Червоні ніколи не входять у пресети (`preset_tweaks` їх відкидає, а
  `apply_preset` ще раз перевіряє) і вмикаються лише вручну з окремим
  підтвердженням.
- ЕФЕКТ: «помітний» / «невеликий» / «залежить від ПК» — чесна оцінка, без
  обіцянок FPS.

Бекапи: перед першою зміною будь-якого твіка (`registry_tweaks_backup_done` у
data.json) — точка відновлення Windows (`Checkpoint-Computer`). І перед першою
зміною кожного ключа реєстру — його експорт у backups/ як .reg-файл з датою
(`registry_tweaks_backed_up_keys` — які ключі вже збережено; новий твік,
доданий у пізнішій версії, теж отримає свій бекап).

Окремо для кожного твіка — при першій його зміні — початковий стан
зберігається в `registry_tweaks_initial_state`, звідки його відновлює
«Повернути все як було» (і, для розділу «Вигляд» окремо, «Повернути гарну
Windows»).
"""

from __future__ import annotations

import ctypes
import os
from core import paths as _paths
import subprocess
import threading
import winreg
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from core.admin import is_admin
from core.logging_setup import get_audit_logger, get_logger
from core.app_data import load_data, update_data
from core.i18n import TDict, t

_logger = get_logger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BACKUPS_DIR = _paths.user_file("backups")

RISK_SAFE = "safe"
RISK_CAUTION = "caution"
RISK_DANGER = "danger"

EFFECT_NOTICEABLE = "noticeable"
EFFECT_SMALL = "small"
EFFECT_DEPENDS = "depends"

EFFECT_LABELS = TDict({
    EFFECT_NOTICEABLE: "tweaks.effect.noticeable",
    EFFECT_SMALL: "tweaks.effect.small",
    EFFECT_DEPENDS: "tweaks.effect.depends",
})

GROUP_GAMES = "games"
GROUP_NETWORK = "network"
GROUP_INPUT = "input"
GROUP_APPEARANCE = "appearance"
GROUP_PRIVACY = "privacy"
GROUP_SYSTEM = "system"

GROUP_ORDER: tuple[str, ...] = (
    GROUP_GAMES, GROUP_NETWORK, GROUP_INPUT, GROUP_APPEARANCE, GROUP_PRIVACY, GROUP_SYSTEM,
)
GROUP_LABELS = TDict({
    GROUP_GAMES: "tweaks.group.games",
    GROUP_NETWORK: "tweaks.group.network",
    GROUP_INPUT: "tweaks.group.input",
    GROUP_APPEARANCE: "tweaks.group.appearance",
    GROUP_PRIVACY: "tweaks.group.privacy",
    GROUP_SYSTEM: "tweaks.group.system",
})

# Твіки блоку "Режим Windows" (перемикаються разом кнопками
# «Максимальна швидкодія» / «Повернути гарну Windows»).
APPEARANCE_TWEAK_IDS: tuple[str, ...] = (
    "transparency",
    "window_menu_anim",
    "shadows_taskbar_anim",
    "menu_show_delay",
    "visual_fx_performance",
)

PRESET_SAFE = "safe"
PRESET_BALANCED = "balanced"
PRESET_MAX = "max"

_HIVE_NAMES = {
    winreg.HKEY_CURRENT_USER: "HKCU",
    winreg.HKEY_LOCAL_MACHINE: "HKLM",
}

_SERVICES = r"SYSTEM\CurrentControlSet\Services"
_TCP_INTERFACES = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
_POWER = r"SYSTEM\CurrentControlSet\Control\Power"
_HVCI = r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity"
_HVCI_POLICY = r"SOFTWARE\Policies\Microsoft\Windows\DeviceGuard"


@dataclass(frozen=True)
class RegEntry:
    hive: int
    subkey: str
    name: str
    vtype: str  # "dword" | "sz"
    on_value: object
    # None — у "вимкненому" стані значення видаляється (типовий стан Windows —
    # значення відсутнє, тож повернення не залишає слідів).
    off_value: object


@dataclass(frozen=True)
class Tweak:
    id: str
    # ключі перекладів (locales/*.json); текст поточною мовою — властивості title/description/risk_note
    title_key: str
    description_key: str
    risk: str
    group: str
    entries: tuple[RegEntry, ...] = ()
    effect: str = EFFECT_SMALL
    requires_reboot: bool = False
    requires_logoff: bool = False
    # Яке значення перемикача показувати, якщо значення реєстру відсутнє
    # (типовий стан Windows "з коробки" для цього твіка).
    missing_state: bool = False
    # Побічний ефект / ризик простими словами — показується в підтвердженні
    # (для жовтих — поруч з описом, для червоних — окремим попередженням).
    risk_note_key: str = ""
    # Після зміни пропонувати перезапуск Провідника («Застосувати зараз»).
    needs_explorer: bool = False
    # Записи, які визначаються на льоту (напр. по мережевих адаптерах) — замість entries.
    entries_fn: Callable[[], tuple[RegEntry, ...]] | None = field(default=None, compare=False)
    # Власне зчитування стану (коли він не зводиться до значень entries).
    state_fn: Callable[[], bool] | None = field(default=None, compare=False)
    # Дія після запису реєстру: apply_fn(enabled) -> (успіх, помилка).
    apply_fn: Callable[[bool], tuple[bool, str]] | None = field(default=None, compare=False)
    # Чому твік зараз не можна увімкнути ("" — можна). Має бути швидким.
    check_fn: Callable[[], str] | None = field(default=None, compare=False)
    # Додаткова довідка до опису (напр. скільки місця звільниться).
    detail_fn: Callable[[], str] | None = field(default=None, compare=False)
    # Ключі реєстру, які треба зберегти в .reg перед зміною, крім ключів entries.
    backup_keys: tuple[tuple[int, str], ...] = ()
    # Потрібні права адміністратора, навіть якщо entries лише в HKCU.
    needs_admin: bool = False

    @property
    def title(self) -> str:
        return t(self.title_key)

    @property
    def description(self) -> str:
        return t(self.description_key)

    @property
    def risk_note(self) -> str:
        return t(self.risk_note_key) if self.risk_note_key else ""


@dataclass(frozen=True)
class SettingsLink:
    """Налаштування, яке не можна надійно змінити з реєстру (Windows зберігає
    його у двійковому форматі CloudStore) — показується рядком із кнопкою, що
    відкриває потрібну сторінку «Параметрів», а не фальшивим перемикачем."""
    group: str
    title_key: str
    description_key: str
    uri: str

    @property
    def title(self) -> str:
        return t(self.title_key)

    @property
    def description(self) -> str:
        return t(self.description_key)


# ------------------------------------------------------- допоміжні для твіків

def _run(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        _logger.error("Failed to run %s: %s", " ".join(args), exc)
        return None


def _service_disabled(name: str) -> bool:
    return _read_value(winreg.HKEY_LOCAL_MACHINE, rf"{_SERVICES}\{name}", "Start") == 4


def _set_service_disabled(name: str, disabled: bool, enabled_start: str) -> tuple[bool, str]:
    """Змінює тип запуску служби (sc config) і зупиняє/запускає її одразу.
    Зупинка/запуск — найкраще зусилля: якщо служба вже в потрібному стані, sc
    поверне помилку, яку тут ігноруємо; важливий лише тип запуску."""
    result = _run(["sc.exe", "config", name, "start=", "disabled" if disabled else enabled_start])
    if result is None or result.returncode != 0:
        out = result.stdout.decode("cp866", "replace").strip() if result is not None else ""
        _logger.error("sc config %s: %s", name, out)
        return False, t("tweaks.err.service_start_type", name=name)
    _run(["sc.exe", "stop" if disabled else "start", name], timeout=30)
    _logger.info("Service %s: %s", name, "disabled" if disabled else f"startup type {enabled_start}")
    return True, ""


def _hibernation_off() -> bool:
    value = _read_value(winreg.HKEY_LOCAL_MACHINE, _POWER, "HibernateEnabled")
    if value is not None:
        return value == 0
    return not os.path.exists(_hiberfil_path())


def _hiberfil_path() -> str:
    return os.path.join(os.environ.get("SystemDrive", "C:") + "\\", "hiberfil.sys")


def _set_hibernation_off(off: bool) -> tuple[bool, str]:
    result = _run(["powercfg.exe", "/hibernate", "off" if off else "on"])
    if result is None or result.returncode != 0:
        return False, t("tweaks.err.hibernation")
    return True, ""


def _hibernation_detail() -> str:
    try:
        size = os.path.getsize(_hiberfil_path())
    except OSError:
        return ""
    return t("tweaks.detail.hiberfil", size_gb=size / 1024 ** 3)


def _active_interfaces() -> list[str]:
    """Мережеві адаптери з IPv4-адресою (DHCP або статичною)."""
    result = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _TCP_INTERFACES, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    guid = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                sub = rf"{_TCP_INTERFACES}\{guid}"
                addresses = []
                dhcp = _read_value(winreg.HKEY_LOCAL_MACHINE, sub, "DhcpIPAddress")
                if isinstance(dhcp, str):
                    addresses.append(dhcp)
                static = _read_value(winreg.HKEY_LOCAL_MACHINE, sub, "IPAddress")
                if isinstance(static, list):
                    addresses.extend(static)
                if any(a and a != "0.0.0.0" for a in addresses):
                    result.append(guid)
    except OSError as exc:
        _logger.error("Failed to read the list of network adapters: %s", exc)
    return sorted(result)


def _nagle_entries() -> tuple[RegEntry, ...]:
    entries = []
    for guid in _active_interfaces():
        sub = rf"{_TCP_INTERFACES}\{guid}"
        entries.append(RegEntry(winreg.HKEY_LOCAL_MACHINE, sub, "TcpAckFrequency", "dword", 1, None))
        entries.append(RegEntry(winreg.HKEY_LOCAL_MACHINE, sub, "TCPNoDelay", "dword", 1, None))
    return tuple(entries)


def _nagle_check() -> str:
    return "" if _active_interfaces() else t("tweaks.block.no_adapter")


# Тип системного диска визначається у фоні (PowerShell, ~1 с): detect_system_disk().
_disk_lock = threading.Lock()
_system_disk_kind: str | None = None  # "SSD" / "HDD" / "Unspecified" / "" (не вдалося)


def detect_system_disk() -> str:
    """Тип носія системного диска (кешується). Викликати з фонового потоку."""
    global _system_disk_kind
    with _disk_lock:
        if _system_disk_kind is not None:
            return _system_disk_kind
        letter = os.environ.get("SystemDrive", "C:").rstrip(":\\")
        script = (
            f"$n = (Get-Partition -DriveLetter '{letter}' -ErrorAction Stop).DiskNumber; "
            "(Get-PhysicalDisk | Where-Object DeviceId -eq \"$n\" | Select-Object -First 1).MediaType"
        )
        result = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=30)
        kind = ""
        if result is not None and result.returncode == 0:
            kind = result.stdout.decode("utf-8", "replace").strip()
        if not kind:
            _logger.error("Failed to detect the system disk type")
        _system_disk_kind = kind
        return kind


def system_disk_kind() -> str | None:
    """Кешований результат detect_system_disk(); None — ще не визначали."""
    return _system_disk_kind


def _sysmain_check() -> str:
    kind = _system_disk_kind
    if kind is None:
        return t("tweaks.block.detecting_disk")
    if kind == "SSD":
        return ""
    if kind == "HDD":
        return t("tweaks.block.sysmain_hdd")
    return t("tweaks.block.sysmain_unknown")


def _hvci_detail() -> str:
    if _read_value(winreg.HKEY_LOCAL_MACHINE, _HVCI, "Enabled") is None:
        return t("tweaks.detail.hvci_never_on")
    return ""


def _hvci_check() -> str:
    if _read_value(winreg.HKEY_LOCAL_MACHINE, _HVCI_POLICY, "HypervisorEnforcedCodeIntegrity") is not None:
        return t("tweaks.block.hvci_policy")
    return ""


# SystemParametersInfo — щоб швидкість повтору клавіш застосувалась одразу.
_SPI_SETKEYBOARDSPEED = 0x000B
_SPI_SETKEYBOARDDELAY = 0x0017
_SPIF_SENDCHANGE = 0x0002


def _apply_keyboard(enabled: bool) -> tuple[bool, str]:
    delay = 0 if enabled else 1
    try:
        user32 = ctypes.windll.user32
        user32.SystemParametersInfoW(_SPI_SETKEYBOARDDELAY, delay, None, _SPIF_SENDCHANGE)
        user32.SystemParametersInfoW(_SPI_SETKEYBOARDSPEED, 31, None, _SPIF_SENDCHANGE)
    except (AttributeError, OSError) as exc:  # значення в реєстрі вже записані — діятиме після виходу
        _logger.error("SystemParametersInfo (keyboard): %s", exc)
    return True, ""


_HKCU = winreg.HKEY_CURRENT_USER
_HKLM = winreg.HKEY_LOCAL_MACHINE
_MULTIMEDIA_PROFILE = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"


TWEAKS: tuple[Tweak, ...] = (
    # ------------------------------------------------------------- ІГРИ І ЗАТРИМКА
    Tweak(
        id="game_dvr",
        title_key="tweaks.game_dvr.title",
        description_key="tweaks.game_dvr.desc",
        risk=RISK_SAFE,
        effect=EFFECT_NOTICEABLE,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKCU, r"System\GameConfigStore", "GameDVR_Enabled", "dword", 0, 1),
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", "dword", 0, 1),
        ),
    ),
    Tweak(
        id="game_bar_off",
        title_key="tweaks.game_bar_off.title",
        description_key="tweaks.game_bar_off.desc",
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKCU, r"Software\Microsoft\GameBar", "UseNexusForGameBarEnabled", "dword", 0, 1),
            RegEntry(_HKCU, r"Software\Microsoft\GameBar", "ShowStartupPanel", "dword", 0, None),
            RegEntry(_HKLM, r"SOFTWARE\Policies\Microsoft\Windows\GameDVR", "AllowGameDVR", "dword", 0, None),
        ),
    ),
    Tweak(
        id="game_mode",
        title_key="tweaks.game_mode.title",
        description_key="tweaks.game_mode.desc",
        risk=RISK_SAFE,
        effect=EFFECT_DEPENDS,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKCU, r"Software\Microsoft\GameBar", "AutoGameModeEnabled", "dword", 1, 0),
            RegEntry(_HKCU, r"Software\Microsoft\GameBar", "AllowAutoGameMode", "dword", 1, 0),
        ),
        missing_state=True,
    ),
    Tweak(
        id="fullscreen_opt_off",
        title_key="tweaks.fullscreen_opt_off.title",
        description_key="tweaks.fullscreen_opt_off.desc",
        risk=RISK_CAUTION,
        effect=EFFECT_DEPENDS,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKCU, r"System\GameConfigStore", "GameDVR_FSEBehaviorMode", "dword", 2, 0),
            RegEntry(_HKCU, r"System\GameConfigStore", "GameDVR_HonorUserFSEBehaviorMode", "dword", 1, 0),
            RegEntry(_HKCU, r"System\GameConfigStore", "GameDVR_DXGIHonorFSEWindowsCompatible", "dword", 1, 0),
        ),
        risk_note_key="tweaks.fullscreen_opt_off.risk",
    ),
    Tweak(
        id="gpu_scheduling",
        title_key="tweaks.gpu_scheduling.title",
        description_key="tweaks.gpu_scheduling.desc",
        risk=RISK_CAUTION,
        effect=EFFECT_DEPENDS,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKLM, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode", "dword", 2, 1),
        ),
        requires_reboot=True,
    ),
    Tweak(
        id="game_scheduler_priority",
        title_key="tweaks.game_scheduler_priority.title",
        description_key="tweaks.game_scheduler_priority.desc",
        risk=RISK_CAUTION,
        effect=EFFECT_SMALL,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKLM, _MULTIMEDIA_PROFILE + r"\Tasks\Games", "GPU Priority", "dword", 8, 8),
            RegEntry(_HKLM, _MULTIMEDIA_PROFILE + r"\Tasks\Games", "Priority", "dword", 6, 2),
            RegEntry(_HKLM, _MULTIMEDIA_PROFILE + r"\Tasks\Games", "Scheduling Category", "sz", "High", "Medium"),
        ),
    ),
    Tweak(
        id="system_responsiveness",
        title_key="tweaks.system_responsiveness.title",
        description_key="tweaks.system_responsiveness.desc",
        risk=RISK_CAUTION,
        effect=EFFECT_SMALL,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKLM, _MULTIMEDIA_PROFILE, "SystemResponsiveness", "dword", 10, 20),
        ),
    ),
    Tweak(
        id="network_throttling_off",
        title_key="tweaks.network_throttling_off.title",
        description_key="tweaks.network_throttling_off.desc",
        risk=RISK_CAUTION,
        effect=EFFECT_SMALL,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKLM, _MULTIMEDIA_PROFILE, "NetworkThrottlingIndex", "dword", 0xFFFFFFFF, 10),
        ),
        risk_note_key="tweaks.network_throttling_off.risk",
    ),
    Tweak(
        id="foreground_priority",
        title_key="tweaks.foreground_priority.title",
        description_key="tweaks.foreground_priority.desc",
        risk=RISK_CAUTION,
        effect=EFFECT_SMALL,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKLM, r"SYSTEM\CurrentControlSet\Control\PriorityControl",
                     "Win32PrioritySeparation", "dword", 0x26, 0x2),
        ),
        risk_note_key="tweaks.foreground_priority.risk",
    ),
    Tweak(
        id="power_throttling_off",
        title_key="tweaks.power_throttling_off.title",
        description_key="tweaks.power_throttling_off.desc",
        risk=RISK_CAUTION,
        effect=EFFECT_DEPENDS,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKLM, _POWER + r"\PowerThrottling", "PowerThrottlingOff", "dword", 1, None),
        ),
        requires_reboot=True,
        risk_note_key="tweaks.power_throttling_off.risk",
    ),
    Tweak(
        id="mpo_off",
        title_key="tweaks.mpo_off.title",
        description_key="tweaks.mpo_off.desc",
        risk=RISK_CAUTION,
        effect=EFFECT_DEPENDS,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKLM, r"SOFTWARE\Microsoft\Windows\Dwm", "OverlayTestMode", "dword", 5, None),
        ),
        requires_reboot=True,
        risk_note_key="tweaks.mpo_off.risk",
    ),
    Tweak(
        id="hvci_off",
        title_key="tweaks.hvci_off.title",
        description_key="tweaks.hvci_off.desc",
        risk=RISK_DANGER,
        effect=EFFECT_DEPENDS,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKLM, _HVCI, "Enabled", "dword", 0, 1),
        ),
        missing_state=True,  # ключа немає — цілісність пам'яті ніколи не вмикалась
        requires_reboot=True,
        check_fn=_hvci_check,
        detail_fn=_hvci_detail,
        risk_note_key="tweaks.hvci_off.risk",
    ),
    # ------------------------------------------------------------------ МЕРЕЖА
    Tweak(
        id="nagle_off",
        title_key="tweaks.nagle_off.title",
        description_key="tweaks.nagle_off.desc",
        risk=RISK_CAUTION,
        effect=EFFECT_DEPENDS,
        group=GROUP_NETWORK,
        entries_fn=_nagle_entries,
        check_fn=_nagle_check,
        backup_keys=((_HKLM, _TCP_INTERFACES),),
        requires_reboot=True,
        risk_note_key="tweaks.nagle_off.risk",
    ),
    Tweak(
        id="delivery_optimization_p2p_off",
        title_key="tweaks.delivery_optimization_p2p_off.title",
        description_key="tweaks.delivery_optimization_p2p_off.desc",
        risk=RISK_SAFE,
        effect=EFFECT_DEPENDS,
        group=GROUP_NETWORK,
        entries=(
            RegEntry(_HKLM, r"SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization",
                     "DODownloadMode", "dword", 0, None),
        ),
    ),
    # --------------------------------------------------- МИША І КЛАВІАТУРА
    Tweak(
        id="mouse_accel",
        title_key="tweaks.mouse_accel.title",
        description_key="tweaks.mouse_accel.desc",
        risk=RISK_SAFE,
        effect=EFFECT_NOTICEABLE,
        group=GROUP_INPUT,
        entries=(
            RegEntry(_HKCU, r"Control Panel\Mouse", "MouseSpeed", "sz", "0", "1"),
            RegEntry(_HKCU, r"Control Panel\Mouse", "MouseThreshold1", "sz", "0", "6"),
            RegEntry(_HKCU, r"Control Panel\Mouse", "MouseThreshold2", "sz", "0", "10"),
        ),
        requires_logoff=True,
    ),
    Tweak(
        id="keyboard_repeat",
        title_key="tweaks.keyboard_repeat.title",
        description_key="tweaks.keyboard_repeat.desc",
        risk=RISK_SAFE,
        effect=EFFECT_NOTICEABLE,
        group=GROUP_INPUT,
        entries=(
            RegEntry(_HKCU, r"Control Panel\Keyboard", "KeyboardDelay", "sz", "0", "1"),
            RegEntry(_HKCU, r"Control Panel\Keyboard", "KeyboardSpeed", "sz", "31", "31"),
        ),
        apply_fn=_apply_keyboard,
    ),
    Tweak(
        id="sticky_keys",
        title_key="tweaks.sticky_keys.title",
        description_key="tweaks.sticky_keys.desc",
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_INPUT,
        entries=(
            RegEntry(_HKCU, r"Control Panel\Accessibility\StickyKeys", "Flags", "sz", "506", "510"),
            RegEntry(_HKCU, r"Control Panel\Accessibility\Keyboard Response", "Flags", "sz", "122", "126"),
            RegEntry(_HKCU, r"Control Panel\Accessibility\ToggleKeys", "Flags", "sz", "58", "62"),
        ),
    ),
    # -------------------------------------- ВИГЛЯД ("максимальна швидкодія")
    Tweak(
        id="transparency",
        title_key="tweaks.transparency.title",
        description_key="tweaks.transparency.desc",
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_APPEARANCE,
        entries=(
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                     "EnableTransparency", "dword", 0, 1),
        ),
        needs_explorer=True,
    ),
    Tweak(
        id="window_menu_anim",
        title_key="tweaks.window_menu_anim.title",
        description_key="tweaks.window_menu_anim.desc",
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_APPEARANCE,
        entries=(
            RegEntry(_HKCU, r"Control Panel\Desktop\WindowMetrics", "MinAnimate", "sz", "0", "1"),
            RegEntry(_HKCU, r"Control Panel\Desktop", "MenuAnimation", "sz", "0", "1"),
        ),
        requires_logoff=True,
        needs_explorer=True,
    ),
    Tweak(
        id="shadows_taskbar_anim",
        title_key="tweaks.shadows_taskbar_anim.title",
        description_key="tweaks.shadows_taskbar_anim.desc",
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_APPEARANCE,
        entries=(
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                     "ListviewShadow", "dword", 0, 1),
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                     "ListviewAlphaSelect", "dword", 0, 1),
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                     "TaskbarAnimations", "dword", 0, 1),
            RegEntry(_HKCU, r"Software\Microsoft\Windows\DWM", "EnableAeroPeek", "dword", 0, 1),
        ),
        requires_logoff=True,
        needs_explorer=True,
    ),
    Tweak(
        id="menu_show_delay",
        title_key="tweaks.menu_show_delay.title",
        description_key="tweaks.menu_show_delay.desc",
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_APPEARANCE,
        entries=(
            RegEntry(_HKCU, r"Control Panel\Desktop", "MenuShowDelay", "sz", "0", "400"),
        ),
        needs_explorer=True,
    ),
    Tweak(
        id="visual_fx_performance",
        title_key="tweaks.visual_fx_performance.title",
        description_key="tweaks.visual_fx_performance.desc",
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_APPEARANCE,
        entries=(
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects",
                     "VisualFXSetting", "dword", 2, 0),
            RegEntry(_HKCU, r"Control Panel\Desktop", "DragFullWindows", "sz", "0", "1"),
        ),
        requires_logoff=True,
        needs_explorer=True,
    ),
    # ---------------------------------------------- КОНФІДЕНЦІЙНІСТЬ І ФОН
    Tweak(
        id="tips_ads",
        title_key="tweaks.tips_ads.title",
        description_key="tweaks.tips_ads.desc",
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_PRIVACY,
        entries=tuple(
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager", name, "dword", 0, 1)
            for name in (
                "SubscribedContent-338388Enabled", "SubscribedContent-338389Enabled",
                "RotatingLockScreenOverlayEnabled", "SoftLandingEnabled", "SystemPaneSuggestionsEnabled",
            )
        ),
        requires_logoff=True,
    ),
    Tweak(
        id="advertising_id",
        title_key="tweaks.advertising_id.title",
        description_key="tweaks.advertising_id.desc",
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_PRIVACY,
        entries=(
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled", "dword", 0, 1),
        ),
    ),
    Tweak(
        id="bing_search",
        title_key="tweaks.bing_search.title",
        description_key="tweaks.bing_search.desc",
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_PRIVACY,
        entries=(
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\Search", "BingSearchEnabled", "dword", 0, 1),
            RegEntry(_HKCU, r"Software\Policies\Microsoft\Windows\Explorer",
                     "DisableSearchBoxSuggestions", "dword", 1, 0),
        ),
        requires_logoff=True,
    ),
    Tweak(
        id="background_apps",
        title_key="tweaks.background_apps.title",
        description_key="tweaks.background_apps.desc",
        risk=RISK_CAUTION,
        effect=EFFECT_SMALL,
        group=GROUP_PRIVACY,
        entries=(
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications",
                     "GlobalUserDisabled", "dword", 1, 0),
        ),
        requires_logoff=True,
    ),
    Tweak(
        id="telemetry",
        title_key="tweaks.telemetry.title",
        description_key="tweaks.telemetry.desc",
        risk=RISK_CAUTION,
        effect=EFFECT_SMALL,
        group=GROUP_PRIVACY,
        entries=(
            RegEntry(_HKLM, r"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry", "dword", 0, 1),
        ),
    ),
    # ------------------------------------------------------------ СИСТЕМА
    Tweak(
        id="hibernation_off",
        title_key="tweaks.hibernation_off.title",
        description_key="tweaks.hibernation_off.desc",
        risk=RISK_SAFE,
        effect=EFFECT_NOTICEABLE,
        group=GROUP_SYSTEM,
        state_fn=_hibernation_off,
        apply_fn=_set_hibernation_off,
        detail_fn=_hibernation_detail,
        backup_keys=((_HKLM, _POWER),),
        needs_admin=True,
    ),
    Tweak(
        id="search_indexing_off",
        title_key="tweaks.search_indexing_off.title",
        description_key="tweaks.search_indexing_off.desc",
        risk=RISK_CAUTION,
        effect=EFFECT_SMALL,
        group=GROUP_SYSTEM,
        state_fn=lambda: _service_disabled("WSearch"),
        apply_fn=lambda enabled: _set_service_disabled("WSearch", enabled, "delayed-auto"),
        backup_keys=((_HKLM, rf"{_SERVICES}\WSearch"),),
        needs_admin=True,
        risk_note_key="tweaks.search_indexing_off.risk",
    ),
    Tweak(
        id="sysmain_off",
        title_key="tweaks.sysmain_off.title",
        description_key="tweaks.sysmain_off.desc",
        risk=RISK_CAUTION,
        effect=EFFECT_SMALL,
        group=GROUP_SYSTEM,
        state_fn=lambda: _service_disabled("SysMain"),
        apply_fn=lambda enabled: _set_service_disabled("SysMain", enabled, "auto"),
        check_fn=_sysmain_check,
        backup_keys=((_HKLM, rf"{_SERVICES}\SysMain"),),
        needs_admin=True,
        risk_note_key="tweaks.sysmain_off.risk",
    ),
    Tweak(
        id="edge_startup_boost_off",
        title_key="tweaks.edge_startup_boost_off.title",
        description_key="tweaks.edge_startup_boost_off.desc",
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_SYSTEM,
        entries=(
            RegEntry(_HKLM, r"SOFTWARE\Policies\Microsoft\Edge", "StartupBoostEnabled", "dword", 0, None),
        ),
    ),
    Tweak(
        id="widgets_off",
        title_key="tweaks.widgets_off.title",
        description_key="tweaks.widgets_off.desc",
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_SYSTEM,
        entries=(
            RegEntry(_HKLM, r"SOFTWARE\Policies\Microsoft\Dsh", "AllowNewsAndInterests", "dword", 0, None),
        ),
        requires_logoff=True,
        needs_explorer=True,
    ),
    Tweak(
        id="copilot_off",
        title_key="tweaks.copilot_off.title",
        description_key="tweaks.copilot_off.desc",
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_SYSTEM,
        entries=(
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                     "ShowCopilotButton", "dword", 0, None),
        ),
        needs_explorer=True,
    ),
    Tweak(
        id="show_hidden_ext",
        title_key="tweaks.show_hidden_ext.title",
        description_key="tweaks.show_hidden_ext.desc",
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_SYSTEM,
        entries=(
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced", "HideFileExt", "dword", 0, 1),
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced", "Hidden", "dword", 1, 2),
        ),
        requires_logoff=True,
    ),
    Tweak(
        id="no_auto_suggested_apps",
        title_key="tweaks.no_auto_suggested_apps.title",
        description_key="tweaks.no_auto_suggested_apps.desc",
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_SYSTEM,
        entries=(
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
                     "SilentInstalledAppsEnabled", "dword", 0, 1),
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
                     "PreInstalledAppsEnabled", "dword", 0, 1),
        ),
    ),
)

SETTINGS_LINKS: tuple[SettingsLink, ...] = (
    SettingsLink(
        group=GROUP_SYSTEM,
        title_key="tweaks.link.notifications.title",
        description_key="tweaks.link.notifications.desc",
        uri="ms-settings:notifications",
    ),
)

_TWEAKS_BY_ID = {tw.id: tw for tw in TWEAKS}


def get_tweak(tweak_id: str) -> Tweak | None:
    return _TWEAKS_BY_ID.get(tweak_id)


def get_appearance_tweaks() -> list[Tweak]:
    return [tw for tw in TWEAKS if tw.id in APPEARANCE_TWEAK_IDS]


# --------------------------------------------------------------- реєстр io

def _read_value(hive: int, subkey: str, name: str):
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
            value, _vtype = winreg.QueryValueEx(key, name)
            return value
    except OSError:
        return None


def _key_exists(hive: int, subkey: str) -> bool:
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ):
            return True
    except OSError:
        return False


def _write_value(entry: RegEntry, value) -> bool:
    vtype = winreg.REG_DWORD if entry.vtype == "dword" else winreg.REG_SZ
    try:
        with winreg.CreateKeyEx(entry.hive, entry.subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, entry.name, 0, vtype, value)
        return True
    except OSError as exc:
        _logger.error("Failed to write %s\\%s: %s", entry.subkey, entry.name, exc)
        return False


def _delete_value(entry: RegEntry) -> bool:
    try:
        with winreg.OpenKey(entry.hive, entry.subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, entry.name)
        return True
    except FileNotFoundError:
        return True  # значення (чи ключа) вже немає — це і є потрібний стан
    except OSError as exc:
        _logger.error("Failed to delete %s\\%s: %s", entry.subkey, entry.name, exc)
        return False


def tweak_entries(tweak: Tweak) -> tuple[RegEntry, ...]:
    return tweak.entries_fn() if tweak.entries_fn is not None else tweak.entries


def entry_requires_admin(entry: RegEntry) -> bool:
    return entry.hive == winreg.HKEY_LOCAL_MACHINE


def tweak_requires_admin(tweak: Tweak) -> bool:
    return tweak.needs_admin or any(entry_requires_admin(e) for e in tweak_entries(tweak))


def get_state(tweak: Tweak) -> bool:
    """Реальний поточний стан твіка: True лише якщо УСІ його значення реєстру
    відповідають "увімкненому" стану (або так каже state_fn)."""
    if tweak.state_fn is not None:
        return tweak.state_fn()
    entries = tweak_entries(tweak)
    if not entries:
        return False
    for entry in entries:
        value = _read_value(entry.hive, entry.subkey, entry.name)
        if value is None:
            if not tweak.missing_state:
                return False
            continue
        if value != entry.on_value:
            return False
    return True


_ACCESS_DENIED = 5
_protected_cache: dict[str, bool] = {}


def _entry_writable(entry: RegEntry) -> bool:
    """Чи дозволяє Windows записати це значення. Деякі (напр. Widgets) захищені від зміни
    навіть для адміністратора — за іменем значення. Перевірка записує те саме значення, що вже
    є (або тимчасове, яке одразу видаляє), тож нічого не змінює. False — лише при "Відмовлено в доступі"."""
    vtype = winreg.REG_DWORD if entry.vtype == "dword" else winreg.REG_SZ
    existed = _key_exists(entry.hive, entry.subkey)
    try:
        with winreg.CreateKeyEx(entry.hive, entry.subkey, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            try:
                value, old_type = winreg.QueryValueEx(key, entry.name)
            except FileNotFoundError:
                winreg.SetValueEx(key, entry.name, 0, vtype, entry.on_value)
                winreg.DeleteValue(key, entry.name)
            else:
                winreg.SetValueEx(key, entry.name, 0, old_type, value)
        return True
    except OSError as exc:
        return getattr(exc, "winerror", None) != _ACCESS_DENIED
    finally:
        if not existed:
            try:
                winreg.DeleteKey(entry.hive, entry.subkey)  # лише порожній ключ, який ми щойно створили
            except OSError:
                pass


def is_protected(tweak: Tweak) -> bool:
    """Windows на цій збірці забороняє змінювати значення цього твіка (UCPD та подібні захисти)."""
    if tweak.id in _protected_cache:
        return _protected_cache[tweak.id]
    protected = False
    for entry in tweak.entries:  # динамічні (entries_fn) не перевіряємо
        if entry_requires_admin(entry) and not is_admin():
            continue  # без прав адміна відмову не відрізнити від захисту
        if not _entry_writable(entry):
            _logger.warning("Tweak \"%s\": Windows blocks writing %s: %s even for an administrator", tweak.id,
                            entry.subkey, entry.name)
            protected = True
            break
    _protected_cache[tweak.id] = protected
    return protected


def blocked_reason(tweak: Tweak) -> str:
    """Чому твік зараз не можна увімкнути ("" — можна). Вимкнути (повернути) можна завжди."""
    if is_protected(tweak):
        return t("tweaks.unavailable.protected")
    return tweak.check_fn() if tweak.check_fn is not None else ""


def detail_text(tweak: Tweak) -> str:
    return tweak.detail_fn() if tweak.detail_fn is not None else ""


# ------------------------------------------------------------------ backup

def _create_restore_point() -> bool:
    if not is_admin():
        return False
    result = _run(
        [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Checkpoint-Computer -Description 'Lagnix: registry tweaks' -RestorePointType 'MODIFY_SETTINGS'",
        ],
        timeout=90,
    )
    return result is not None and result.returncode == 0


def _export_key_backup(hive_name: str, subkey: str, dest_path: str) -> bool:
    result = _run(["reg", "export", f"{hive_name}\\{subkey}", dest_path, "/y"], timeout=15)
    return result is not None and result.returncode == 0


def _tweak_keys(tweak: Tweak) -> list[tuple[int, str]]:
    keys: list[tuple[int, str]] = []
    for hive, subkey in [(e.hive, e.subkey) for e in tweak_entries(tweak)] + list(tweak.backup_keys):
        if (hive, subkey) not in keys:
            keys.append((hive, subkey))
    return keys


def _backup_keys(keys: list[tuple[int, str]]) -> None:
    """Експортує в .reg ключі, які ще не зберігались. Ключ, якого ще немає, не
    експортується (до зміни він не існував — це і є його "як було")."""
    done = set(load_data().get("registry_tweaks_backed_up_keys", []))
    pending = [(h, s) for h, s in keys if f"{_HIVE_NAMES[h]}\\{s}" not in done]
    if not pending:
        return
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for hive, subkey in pending:
        hive_name = _HIVE_NAMES[hive]
        tag = f"{hive_name}\\{subkey}"
        if _key_exists(hive, subkey):
            safe_subkey = subkey.replace("\\", "_").replace(" ", "_")
            path = os.path.join(BACKUPS_DIR, f"{stamp}_{hive_name}_{safe_subkey}.reg")
            if not _export_key_backup(hive_name, subkey, path):
                _logger.error("Backup of %s failed — the key is not marked as saved", tag)
                continue
        else:
            _logger.info("Backup of %s: the key does not exist yet, nothing to save", tag)
        done.add(tag)
    update_data("registry_tweaks_backed_up_keys", sorted(done))


def backup_registry_keys(keys: list[tuple[int, str]]) -> None:
    """Публічний вхід для інших модулів (автозапуск): .reg-бекап ключів перед зміною."""
    _backup_keys(keys)


def _ensure_backup_for(tweak: Tweak) -> None:
    data = load_data()

    initial_state = dict(data.get("registry_tweaks_initial_state", {}))
    if tweak.id not in initial_state:
        initial_state[tweak.id] = get_state(tweak)
        update_data("registry_tweaks_initial_state", initial_state)

    if not data.get("registry_tweaks_backup_done", False):
        if _create_restore_point():
            get_audit_logger().info("Windows restore point created before the first registry tweak")
        else:
            _logger.error("Windows restore point was NOT created (System Restore off or limited to one per 24 h?); "
                          "the .reg backups of the keys are still made")
        keys: list[tuple[int, str]] = []
        for tw in TWEAKS:
            keys.extend(k for k in _tweak_keys(tw) if k not in keys)
        _backup_keys(keys)
        update_data("registry_tweaks_backup_done", True)
    else:
        _backup_keys(_tweak_keys(tweak))


def has_initial_state() -> bool:
    return bool(load_data().get("registry_tweaks_initial_state"))


# ------------------------------------------------------------------ apply

def _query_value(entry: RegEntry) -> tuple | None:
    """(значення, тип) або None, якщо значення немає."""
    try:
        with winreg.OpenKey(entry.hive, entry.subkey, 0, winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key, entry.name)
    except OSError:
        return None


def _rollback(previous: list[tuple[RegEntry, tuple | None]]) -> None:
    """Повертає значення реєстру до стану до спроби (у зворотному порядку)."""
    for entry, old in reversed(previous):
        try:
            if old is None:
                _delete_value(entry)
            else:
                with winreg.CreateKeyEx(entry.hive, entry.subkey, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, entry.name, 0, old[1], old[0])
        except OSError as exc:
            _logger.error("Rollback of %s: %s failed: %s", entry.subkey, entry.name, exc)


def set_tweak(tweak: Tweak, enabled: bool) -> tuple[bool, str]:
    if tweak_requires_admin(tweak) and not is_admin():
        _logger.error("No administrator rights to change \"%s\"", tweak.title)
        return False, t("tweaks.err.need_admin")
    if enabled:
        reason = blocked_reason(tweak)
        if reason:
            return False, reason

    _ensure_backup_for(tweak)

    # Усе або нічого: запам'ятовуємо попередні значення й при будь-якій невдачі повертаємо вже записане.
    previous: list[tuple[RegEntry, tuple | None]] = []
    for entry in tweak_entries(tweak):
        value = entry.on_value if enabled else entry.off_value
        previous.append((entry, _query_value(entry)))
        ok = _delete_value(entry) if value is None else _write_value(entry, value)
        if not ok:
            _rollback(previous)
            return False, t("tweaks.err.write_value", name=entry.name)
    if tweak.apply_fn is not None:
        ok, error = tweak.apply_fn(enabled)
        if not ok:
            _rollback(previous)
            return False, error
    get_audit_logger().info("Tweak \"%s\" (%s): %s", tweak.title, tweak.id, "enabled" if enabled else "disabled")
    return True, ""


def apply_tweaks(tweaks: list[Tweak], enabled: bool) -> list[tuple[Tweak, bool, str]]:
    results = []
    for tweak in tweaks:
        if get_state(tweak) == enabled:
            continue
        if enabled and is_protected(tweak):
            continue  # недоступний на цій збірці Windows — не помилка, просто пропускаємо
        success, error = set_tweak(tweak, enabled)
        results.append((tweak, success, error))
    return results


# ------------------------------------------------------------------ пресети

def preset_tweaks(preset: str) -> list[Tweak]:
    """Склад пресета. Червоні («ризиковано») не входять у жоден пресет."""
    def included(tw: Tweak) -> bool:
        if is_protected(tw):
            return False
        if tw.risk == RISK_SAFE:
            return True
        if tw.risk == RISK_CAUTION:
            return preset == PRESET_MAX or (preset == PRESET_BALANCED and tw.effect == EFFECT_SMALL)
        return False

    return [tw for tw in TWEAKS if included(tw)]


def preset_pending(preset: str) -> list[tuple[Tweak, str]]:
    """Твіки пресета, які ще не ввімкнені: (твік, причина, чому не можна; "" — можна)."""
    return [(tw, blocked_reason(tw)) for tw in preset_tweaks(preset) if not get_state(tw)]


def apply_preset(preset: str, tweak_ids: list[str]) -> list[tuple[Tweak, bool, str]]:
    """Вмикає вибрані користувачем твіки пресета. Усе, що не входить у пресет
    (зокрема червоні), відкидається, навіть якщо його id передали."""
    allowed = {tw.id for tw in preset_tweaks(preset)}
    chosen = [tw for tw in TWEAKS if tw.id in tweak_ids and tw.id in allowed]
    return apply_tweaks(chosen, True)


def get_recommended_tweaks() -> list[Tweak]:
    return preset_tweaks(PRESET_SAFE)


def restore_pending() -> list[tuple[Tweak, bool]]:
    """Твіки, змінені Lagnix, які зараз не в початковому стані: (твік, початковий стан)."""
    initial_state = load_data().get("registry_tweaks_initial_state", {})
    result = []
    for tweak in TWEAKS:
        if tweak.id not in initial_state:
            continue
        target = bool(initial_state[tweak.id])
        if get_state(tweak) != target:
            result.append((tweak, target))
    return result


def restore_tweaks(tweak_ids: list[str] | None = None) -> list[tuple[Tweak, bool, str]]:
    """Повертає до початкового стану вибрані (None — усі) змінені твіки."""
    results = []
    for tweak, target in restore_pending():
        if tweak_ids is not None and tweak.id not in tweak_ids:
            continue
        success, error = set_tweak(tweak, target)
        results.append((tweak, success, error))
    return results


def restore_initial_state() -> list[tuple[Tweak, bool, str]]:
    return restore_tweaks(None)


# ------------------------------------------------------ режим Windows (вигляд)

def apply_max_performance() -> list[tuple[Tweak, bool, str]]:
    """Вмикає всі твіки розділу "Вигляд" (кнопка «Максимальна швидкодія")."""
    return apply_tweaks(get_appearance_tweaks(), True)


def restore_appearance_defaults() -> list[tuple[Tweak, bool, str]]:
    """Повертає твіки розділу "Вигляд" до збережених початкових значень
    (кнопка «Повернути гарну Windows»). Якщо твік ще не мав збереженого
    початкового стану — вважається, що типовий стан Windows вимкнений."""
    initial_state = load_data().get("registry_tweaks_initial_state", {})

    results = []
    for tweak in get_appearance_tweaks():
        target = bool(initial_state.get(tweak.id, False))
        if get_state(tweak) == target:
            continue
        success, error = set_tweak(tweak, target)
        results.append((tweak, success, error))
    return results


# Перезапуск Провідника — process_control.restart_explorer (лише після підтвердження).
