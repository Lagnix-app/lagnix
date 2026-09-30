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
import subprocess
import threading
import winreg
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from core.admin import is_admin
from core.logging_setup import get_logger
from core.app_data import load_data, update_data

_logger = get_logger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BACKUPS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups")

RISK_SAFE = "safe"
RISK_CAUTION = "caution"
RISK_DANGER = "danger"

EFFECT_NOTICEABLE = "noticeable"
EFFECT_SMALL = "small"
EFFECT_DEPENDS = "depends"

EFFECT_LABELS: dict[str, str] = {
    EFFECT_NOTICEABLE: "помітний",
    EFFECT_SMALL: "невеликий",
    EFFECT_DEPENDS: "залежить від ПК",
}

GROUP_GAMES = "games"
GROUP_NETWORK = "network"
GROUP_INPUT = "input"
GROUP_APPEARANCE = "appearance"
GROUP_PRIVACY = "privacy"
GROUP_SYSTEM = "system"

GROUP_ORDER: tuple[str, ...] = (
    GROUP_GAMES, GROUP_NETWORK, GROUP_INPUT, GROUP_APPEARANCE, GROUP_PRIVACY, GROUP_SYSTEM,
)
GROUP_LABELS: dict[str, str] = {
    GROUP_GAMES: "Ігри і затримка",
    GROUP_NETWORK: "Мережа",
    GROUP_INPUT: "Миша і клавіатура",
    GROUP_APPEARANCE: "Вигляд",
    GROUP_PRIVACY: "Конфіденційність і фон",
    GROUP_SYSTEM: "Система",
}

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
    title: str
    description: str
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
    risk_note: str = ""
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


@dataclass(frozen=True)
class SettingsLink:
    """Налаштування, яке не можна надійно змінити з реєстру (Windows зберігає
    його у двійковому форматі CloudStore) — показується рядком із кнопкою, що
    відкриває потрібну сторінку «Параметрів», а не фальшивим перемикачем."""
    group: str
    title: str
    description: str
    uri: str


# ------------------------------------------------------- допоміжні для твіків

def _run(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        _logger.error("Не вдалося виконати %s: %s", " ".join(args), exc)
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
        return False, f"Не вдалося змінити тип запуску служби {name}"
    _run(["sc.exe", "stop" if disabled else "start", name], timeout=30)
    _logger.info("Служба %s: %s", name, "вимкнена" if disabled else f"тип запуску {enabled_start}")
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
        return False, "powercfg не зміг змінити гібернацію"
    return True, ""


def _hibernation_detail() -> str:
    try:
        size = os.path.getsize(_hiberfil_path())
    except OSError:
        return ""
    return f"Зараз hiberfil.sys займає {size / 1024 ** 3:.1f} ГБ."


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
        _logger.error("Не вдалося прочитати список мережевих адаптерів: %s", exc)
    return sorted(result)


def _nagle_entries() -> tuple[RegEntry, ...]:
    entries = []
    for guid in _active_interfaces():
        sub = rf"{_TCP_INTERFACES}\{guid}"
        entries.append(RegEntry(winreg.HKEY_LOCAL_MACHINE, sub, "TcpAckFrequency", "dword", 1, None))
        entries.append(RegEntry(winreg.HKEY_LOCAL_MACHINE, sub, "TCPNoDelay", "dword", 1, None))
    return tuple(entries)


def _nagle_check() -> str:
    return "" if _active_interfaces() else "Не знайдено мережевого адаптера з IP-адресою."


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
            _logger.error("Не вдалося визначити тип системного диска")
        _system_disk_kind = kind
        return kind


def system_disk_kind() -> str | None:
    """Кешований результат detect_system_disk(); None — ще не визначали."""
    return _system_disk_kind


def _sysmain_check() -> str:
    kind = _system_disk_kind
    if kind is None:
        return "Визначаю тип системного диска…"
    if kind == "SSD":
        return ""
    if kind == "HDD":
        return "Системний диск — HDD: на ньому SysMain помітно пришвидшує запуск програм, вимикати не варто."
    return "Не вдалося визначити, що системний диск — SSD, тому вимкнення недоступне."


def _hvci_detail() -> str:
    if _read_value(winreg.HKEY_LOCAL_MACHINE, _HVCI, "Enabled") is None:
        return "На цьому ПК цілісність пам'яті й так не вмикалась — тому перемикач уже ввімкнений."
    return ""


def _hvci_check() -> str:
    if _read_value(winreg.HKEY_LOCAL_MACHINE, _HVCI_POLICY, "HypervisorEnforcedCodeIntegrity") is not None:
        return "Цілісність пам'яті задана груповою політикою — змінити її звідси не вийде."
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
        _logger.error("SystemParametersInfo (клавіатура): %s", exc)
    return True, ""


_HKCU = winreg.HKEY_CURRENT_USER
_HKLM = winreg.HKEY_LOCAL_MACHINE
_MULTIMEDIA_PROFILE = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"


TWEAKS: tuple[Tweak, ...] = (
    # ------------------------------------------------------------- ІГРИ І ЗАТРИМКА
    Tweak(
        id="game_dvr",
        title="Xbox Game DVR / фоновий запис",
        description=(
            "Вимикає фоновий запис геймплею через Xbox Game Bar — він може забирати "
            "ресурси процесора й диска під час гри."
        ),
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
        title="Вимкнути Game Bar і його оверлей",
        description=(
            "Game Bar більше не відкриватиметься кнопкою Xbox на геймпаді й не показуватиме "
            "стартову підказку, а запис і трансляція ігор вимикаються політикою. "
            "Сполучення Win+G вручну все ще може відкрити порожню панель."
        ),
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
        title="Game Mode Windows",
        description=(
            "Вбудований режим Windows, який надає грі пріоритет над фоновими "
            "процесами й оновленнями під час запуску."
        ),
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
        title="Повноекранні оптимізації (глобально)",
        description=(
            "Вимикає системну обробку повноекранного режиму Windows одразу для "
            "всіх ігор — у деяких іграх це дає стабільнішу частоту кадрів у "
            "справжньому повноекранному режимі."
        ),
        risk=RISK_CAUTION,
        effect=EFFECT_DEPENDS,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKCU, r"System\GameConfigStore", "GameDVR_FSEBehaviorMode", "dword", 2, 0),
            RegEntry(_HKCU, r"System\GameConfigStore", "GameDVR_HonorUserFSEBehaviorMode", "dword", 1, 0),
            RegEntry(_HKCU, r"System\GameConfigStore", "GameDVR_DXGIHonorFSEWindowsCompatible", "dword", 1, 0),
        ),
        risk_note="Перемикання Alt+Tab у частині ігор може стати повільнішим.",
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
        effect=EFFECT_DEPENDS,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKLM, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode", "dword", 2, 1),
        ),
        requires_reboot=True,
    ),
    Tweak(
        id="game_scheduler_priority",
        title="Пріоритет ігор у планувальнику завдань",
        description=(
            "Піднімає пріоритет процесора й черги GPU для ігор у профілі "
            "мультимедійного планувальника Windows "
            "(SystemProfile\\Tasks\\Games: GPU Priority, Priority, Scheduling Category)."
        ),
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
        title="SystemResponsiveness для мультимедіа",
        description=(
            "Зменшує частку процесора, яку Windows резервує для фонових служб, "
            "на користь мультимедійних та ігрових потоків (SystemResponsiveness = 10)."
        ),
        risk=RISK_CAUTION,
        effect=EFFECT_SMALL,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKLM, _MULTIMEDIA_PROFILE, "SystemResponsiveness", "dword", 10, 20),
        ),
    ),
    Tweak(
        id="network_throttling_off",
        title="Вимкнути обмеження мережі для мультимедіа",
        description=(
            "Поки грає музика чи відео, Windows штучно обмежує обробку мережевих "
            "пакетів. NetworkThrottlingIndex = ffffffff прибирає це обмеження."
        ),
        risk=RISK_CAUTION,
        effect=EFFECT_SMALL,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKLM, _MULTIMEDIA_PROFILE, "NetworkThrottlingIndex", "dword", 0xFFFFFFFF, 10),
        ),
        risk_note="На слабких ПК під час великих завантажень звук може зрідка переривчасто «заїкатися».",
    ),
    Tweak(
        id="foreground_priority",
        title="Пріоритет активного вікна",
        description=(
            "Win32PrioritySeparation = 0x26: програма, з якою ви зараз працюєте (гра), "
            "отримує довші й частіші кванти процесора, ніж фонові."
        ),
        risk=RISK_CAUTION,
        effect=EFFECT_SMALL,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKLM, r"SYSTEM\CurrentControlSet\Control\PriorityControl",
                     "Win32PrioritySeparation", "dword", 0x26, 0x2),
        ),
        risk_note="Фонові задачі (архівація, рендер, завантаження) під час гри йтимуть трохи повільніше.",
    ),
    Tweak(
        id="power_throttling_off",
        title="Вимкнути Power Throttling для фонових програм",
        description=(
            "Windows сповільнює «неважливі» фонові програми, щоб заощадити енергію. "
            "Вимкнення прибирає це сповільнення для всіх програм — корисно, якщо "
            "стрім, запис чи голосовий чат працюють у фоні під час гри."
        ),
        risk=RISK_CAUTION,
        effect=EFFECT_DEPENDS,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKLM, _POWER + r"\PowerThrottling", "PowerThrottlingOff", "dword", 1, None),
        ),
        requires_reboot=True,
        risk_note="На ноутбуці від батареї заряд триматиметься менше, а корпус може бути теплішим.",
    ),
    Tweak(
        id="mpo_off",
        title="Вимкнути MPO (Multiplane Overlay)",
        description=(
            "Допомагає, якщо бачите мерехтіння, чорні спалахи чи короткі фризи в іграх і "
            "браузері — особливо з кількома моніторами різної частоти. Якщо таких проблем "
            "немає, користі не буде. На найновіших збірках Windows 11 може не діяти."
        ),
        risk=RISK_CAUTION,
        effect=EFFECT_DEPENDS,
        group=GROUP_GAMES,
        entries=(
            RegEntry(_HKLM, r"SOFTWARE\Microsoft\Windows\Dwm", "OverlayTestMode", "dword", 5, None),
        ),
        requires_reboot=True,
        risk_note="Може трохи зрости навантаження на відеокарту у віконному режимі й відео.",
    ),
    Tweak(
        id="hvci_off",
        title="Вимкнути ізоляцію ядра (цілісність пам'яті, VBS/HVCI)",
        description=(
            "Цілісність пам'яті перевіряє драйвери за допомогою віртуалізації. Її "
            "вимкнення може дати кілька відсотків FPS, але знижує захист Windows."
        ),
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
        risk_note=(
            "• Шкідливі чи вразливі драйвери зможуть потрапити в ядро системи — це саме "
            "та атака, від якої захищає ця функція.\n"
            "• Деякі античити й ігри перевіряють захист системи і можуть відмовитися "
            "запускатися або вимагати його знову ввімкнути.\n"
            "• Приріст FPS невеликий (кілька відсотків) і є не на кожному ПК.\n"
            "• Зміна діє лише після перезавантаження."
        ),
    ),
    # ------------------------------------------------------------------ МЕРЕЖА
    Tweak(
        id="nagle_off",
        title="Вимкнути алгоритм Нейгла",
        description=(
            "Windows збирає дрібні мережеві пакети в пачки й трохи чекає з "
            "підтвердженнями. TcpAckFrequency = 1 і TCPNoDelay = 1 на активних адаптерах "
            "прибирають це очікування — може знизити затримку в старіших онлайн-іграх, "
            "що працюють через TCP. Більшість сучасних ігор використовують UDP, їм це не допоможе."
        ),
        risk=RISK_CAUTION,
        effect=EFFECT_DEPENDS,
        group=GROUP_NETWORK,
        entries_fn=_nagle_entries,
        check_fn=_nagle_check,
        backup_keys=((_HKLM, _TCP_INTERFACES),),
        requires_reboot=True,
        risk_note=(
            "Трохи більше дрібних пакетів у мережі; на повільному чи мобільному "
            "інтернеті завантаження можуть стати повільнішими. Новий адаптер (інший "
            "Wi-Fi) твік не зачепить — його треба ввімкнути ще раз."
        ),
    ),
    Tweak(
        id="delivery_optimization_p2p_off",
        title="Вимкнути P2P-роздачу оновлень Windows",
        description=(
            "Оптимізація доставки роздає вже завантажені оновлення іншим комп'ютерам "
            "і тягне їх з інших ПК, витрачаючи ваш канал. Після вимкнення оновлення "
            "йдуть лише з серверів Microsoft — самі оновлення працюють як раніше."
        ),
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
        title="Прискорення миші (Enhance pointer precision)",
        description=(
            "Прибирає прискорення курсора, щоб рух миші був однаково передбачуваним "
            "на будь-якій швидкості — важливо для точності прицілювання в іграх."
        ),
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
        title="Мінімальна затримка і максимальна швидкість повтору клавіш",
        description=(
            "Затиснута клавіша почне повторюватися майже одразу й з найбільшою "
            "швидкістю (KeyboardDelay = 0, KeyboardSpeed = 31). Діє відразу."
        ),
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
        title="Гарячі клавіші залипання, фільтрації й перемикання клавіш",
        description=(
            "Вимикає випадкову появу вікон спеціальних можливостей (Sticky/Filter/"
            "Toggle Keys) від багаторазового натискання Shift, утримання клавіш чи "
            "Num Lock під час гри."
        ),
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
        title="Прозорість інтерфейсу",
        description="Вимикає ефект прозорості вікон, меню «Пуск» і панелі завдань.",
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
        title="Анімації вікон і меню",
        description="Вимикає анімацію згортання/розгортання вікон і появи меню.",
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
        title="Тіні, згладжування й анімація панелі завдань",
        description=(
            "Вимикає тінь під підписами значків, прозоре виділення в списках і "
            "анімацію кнопок панелі завдань."
        ),
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
        title="Затримка показу меню",
        description="Прибирає паузу перед розгортанням підменю (MenuShowDelay = 0).",
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
        title="Візуальні ефекти «найкраща швидкодія»",
        description=(
            "Перемикає загальний пресет візуальних ефектів Windows на «Забезпечити "
            "найкращу швидкодію» в параметрах швидкодії системи."
        ),
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
        title="Поради й реклама в Пуск та на екрані блокування",
        description=(
            "Прибирає рекламні плитки, підказки й пропозиції застосунків у меню "
            "Пуск та на екрані блокування."
        ),
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
        title="Рекламний ідентифікатор",
        description=(
            "Забороняє застосункам використовувати рекламний ідентифікатор для "
            "персоналізованої реклами."
        ),
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_PRIVACY,
        entries=(
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled", "dword", 0, 1),
        ),
    ),
    Tweak(
        id="bing_search",
        title="Пошук Bing у меню «Пуск»",
        description="Вимикає веб-результати Bing при пошуку через меню «Пуск».",
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
        title="Фонові застосунки",
        description=(
            "Забороняє застосункам з Microsoft Store працювати у фоні. Може "
            "вплинути на сповіщення деяких програм (пошта, месенджери)."
        ),
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
        title="Телеметрія Windows",
        description=(
            "Знижує обсяг діагностичних даних, які Windows надсилає Microsoft, до "
            "мінімального рівня. На Windows Home/Pro може не вимкнути збір даних "
            "повністю."
        ),
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
        title="Вимкнути гібернацію",
        description=(
            "powercfg -h off: видаляє файл hiberfil.sys на диску C: (зазвичай кілька "
            "гігабайтів). Сон працює як і раніше; зникнуть лише «Гібернація» і "
            "«Швидкий запуск» (після вимкнення ПК стартує з нуля — це навіть надійніше)."
        ),
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
        title="Вимкнути індексування пошуку",
        description=(
            "Зупиняє службу Windows Search, яка постійно сканує файли для швидкого "
            "пошуку. Менше фонової роботи диска й процесора."
        ),
        risk=RISK_CAUTION,
        effect=EFFECT_SMALL,
        group=GROUP_SYSTEM,
        state_fn=lambda: _service_disabled("WSearch"),
        apply_fn=lambda enabled: _set_service_disabled("WSearch", enabled, "delayed-auto"),
        backup_keys=((_HKLM, rf"{_SERVICES}\WSearch"),),
        needs_admin=True,
        risk_note=(
            "Пошук у меню «Пуск» і Провіднику стане помітно повільнішим, а пошук "
            "у пошті Outlook може не працювати."
        ),
    ),
    Tweak(
        id="sysmain_off",
        title="Вимкнути SysMain (лише для SSD)",
        description=(
            "SysMain (колишній Superfetch) заздалегідь підвантажує часто вживані "
            "програми в пам'ять. На SSD це майже не пришвидшує запуск, але дає фонові "
            "звернення до диска. Доступно, лише якщо системний диск — SSD."
        ),
        risk=RISK_CAUTION,
        effect=EFFECT_SMALL,
        group=GROUP_SYSTEM,
        state_fn=lambda: _service_disabled("SysMain"),
        apply_fn=lambda enabled: _set_service_disabled("SysMain", enabled, "auto"),
        check_fn=_sysmain_check,
        backup_keys=((_HKLM, rf"{_SERVICES}\SysMain"),),
        needs_admin=True,
        risk_note="Програми, якими ви давно не користувалися, першого разу можуть відкриватися трохи довше.",
    ),
    Tweak(
        id="edge_startup_boost_off",
        title="Вимкнути Startup Boost у Edge",
        description=(
            "Edge більше не запускатиметься прихованим у фоні разом із Windows. "
            "Налаштовується політикою, тож у Edge з'явиться напис «Вашим браузером "
            "керує організація» — це нормально."
        ),
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_SYSTEM,
        entries=(
            RegEntry(_HKLM, r"SOFTWARE\Policies\Microsoft\Edge", "StartupBoostEnabled", "dword", 0, None),
        ),
    ),
    Tweak(
        id="widgets_copilot_off",
        title="Вимкнути віджети і Copilot на панелі завдань",
        description=(
            "Прибирає панель віджетів (новини, погода) і кнопку Copilot з панелі завдань — "
            "вони тримають у фоні власні процеси з вебвмістом."
        ),
        risk=RISK_SAFE,
        effect=EFFECT_SMALL,
        group=GROUP_SYSTEM,
        entries=(
            RegEntry(_HKLM, r"SOFTWARE\Policies\Microsoft\Dsh", "AllowNewsAndInterests", "dword", 0, None),
            RegEntry(_HKCU, r"Software\Policies\Microsoft\Windows\WindowsCopilot",
                     "TurnOffWindowsCopilot", "dword", 1, None),
            RegEntry(_HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                     "ShowCopilotButton", "dword", 0, None),
        ),
        requires_logoff=True,
        needs_explorer=True,
    ),
    Tweak(
        id="show_hidden_ext",
        title="Розширення файлів і приховані файли",
        description="Показує розширення файлів і приховані файли та папки в Провіднику.",
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
        title="Автоматичне встановлення рекомендованих застосунків",
        description="Забороняє Windows самостійно встановлювати застосунки, які вона «рекомендує».",
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
        title="Сповіщення під час гри і в повноекранному режимі",
        description=(
            "Windows 11 зберігає правила «Не турбувати» у двійковому форматі, який "
            "неможливо надійно змінити з реєстру, тож тут немає перемикача. Відкрийте "
            "«Сповіщення» → «Автоматично вмикати режим "
            "«Не турбувати»» і залиште ввімкненими «Під час гри» та «Під час "
            "використання програми в повноекранному режимі»."
        ),
        uri="ms-settings:notifications",
    ),
)

_TWEAKS_BY_ID = {t.id: t for t in TWEAKS}


def get_tweak(tweak_id: str) -> Tweak | None:
    return _TWEAKS_BY_ID.get(tweak_id)


def get_appearance_tweaks() -> list[Tweak]:
    return [t for t in TWEAKS if t.id in APPEARANCE_TWEAK_IDS]


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
        _logger.error("Не вдалося записати %s\\%s: %s", entry.subkey, entry.name, exc)
        return False


def _delete_value(entry: RegEntry) -> bool:
    try:
        with winreg.OpenKey(entry.hive, entry.subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, entry.name)
        return True
    except FileNotFoundError:
        return True  # значення (чи ключа) вже немає — це і є потрібний стан
    except OSError as exc:
        _logger.error("Не вдалося видалити %s\\%s: %s", entry.subkey, entry.name, exc)
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


def blocked_reason(tweak: Tweak) -> str:
    """Чому твік зараз не можна увімкнути ("" — можна). Вимкнути (повернути) можна завжди."""
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
            "Checkpoint-Computer -Description 'PulseFPS: твіки реєстру' "
            "-RestorePointType 'MODIFY_SETTINGS'",
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
                _logger.error("Бекап %s не вдався — ключ не позначено як збережений", tag)
                continue
        else:
            _logger.info("Бекап %s: ключа ще немає, зберігати нічого", tag)
        done.add(tag)
    update_data("registry_tweaks_backed_up_keys", sorted(done))


def _ensure_backup_for(tweak: Tweak) -> None:
    data = load_data()

    initial_state = dict(data.get("registry_tweaks_initial_state", {}))
    if tweak.id not in initial_state:
        initial_state[tweak.id] = get_state(tweak)
        update_data("registry_tweaks_initial_state", initial_state)

    if not data.get("registry_tweaks_backup_done", False):
        _create_restore_point()
        keys: list[tuple[int, str]] = []
        for t in TWEAKS:
            keys.extend(k for k in _tweak_keys(t) if k not in keys)
        _backup_keys(keys)
        update_data("registry_tweaks_backup_done", True)
    else:
        _backup_keys(_tweak_keys(tweak))


def has_initial_state() -> bool:
    return bool(load_data().get("registry_tweaks_initial_state"))


# ------------------------------------------------------------------ apply

def set_tweak(tweak: Tweak, enabled: bool) -> tuple[bool, str]:
    if tweak_requires_admin(tweak) and not is_admin():
        _logger.error("Немає прав адміністратора для зміни «%s»", tweak.title)
        return False, "Потрібні права адміністратора"
    if enabled:
        reason = blocked_reason(tweak)
        if reason:
            return False, reason

    _ensure_backup_for(tweak)

    for entry in tweak_entries(tweak):
        value = entry.on_value if enabled else entry.off_value
        ok = _delete_value(entry) if value is None else _write_value(entry, value)
        if not ok:
            return False, f"Не вдалося змінити значення «{entry.name}»"
    if tweak.apply_fn is not None:
        ok, error = tweak.apply_fn(enabled)
        if not ok:
            return False, error
    _logger.info("Твік «%s»: %s", tweak.title, "увімкнено" if enabled else "вимкнено")
    return True, ""


def apply_tweaks(tweaks: list[Tweak], enabled: bool) -> list[tuple[Tweak, bool, str]]:
    results = []
    for tweak in tweaks:
        if get_state(tweak) == enabled:
            continue
        success, error = set_tweak(tweak, enabled)
        results.append((tweak, success, error))
    return results


# ------------------------------------------------------------------ пресети

def preset_tweaks(preset: str) -> list[Tweak]:
    """Склад пресета. Червоні («ризиковано») не входять у жоден пресет."""
    def included(t: Tweak) -> bool:
        if t.risk == RISK_SAFE:
            return True
        if t.risk == RISK_CAUTION:
            return preset == PRESET_MAX or (preset == PRESET_BALANCED and t.effect == EFFECT_SMALL)
        return False

    return [t for t in TWEAKS if included(t)]


def preset_pending(preset: str) -> list[tuple[Tweak, str]]:
    """Твіки пресета, які ще не ввімкнені: (твік, причина, чому не можна; "" — можна)."""
    return [(t, blocked_reason(t)) for t in preset_tweaks(preset) if not get_state(t)]


def apply_preset(preset: str, tweak_ids: list[str]) -> list[tuple[Tweak, bool, str]]:
    """Вмикає вибрані користувачем твіки пресета. Усе, що не входить у пресет
    (зокрема червоні), відкидається, навіть якщо його id передали."""
    allowed = {t.id for t in preset_tweaks(preset)}
    chosen = [t for t in TWEAKS if t.id in tweak_ids and t.id in allowed]
    return apply_tweaks(chosen, True)


def get_recommended_tweaks() -> list[Tweak]:
    return preset_tweaks(PRESET_SAFE)


def restore_pending() -> list[tuple[Tweak, bool]]:
    """Твіки, змінені PulseFPS, які зараз не в початковому стані: (твік, початковий стан)."""
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
