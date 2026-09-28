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
відновлює кнопка «Повернути все як було» (і, для розділу «Вигляд» окремо,
кнопка «Повернути гарну Windows»).
"""

from __future__ import annotations

import os
import subprocess
import winreg
from dataclasses import dataclass
from datetime import datetime

from core.admin import is_admin
from core.logging_setup import get_logger
from core.app_data import load_data, update_data

_logger = get_logger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BACKUPS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups")

RISK_SAFE = "safe"
RISK_CAUTION = "caution"

GROUP_GAMES = "games"
GROUP_INPUT = "input"
GROUP_APPEARANCE = "appearance"
GROUP_PRIVACY = "privacy"
GROUP_SYSTEM = "system"

GROUP_ORDER: tuple[str, ...] = (GROUP_GAMES, GROUP_INPUT, GROUP_APPEARANCE, GROUP_PRIVACY, GROUP_SYSTEM)
GROUP_LABELS: dict[str, str] = {
    GROUP_GAMES: "Ігри",
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
    group: str
    entries: tuple[RegEntry, ...]
    requires_reboot: bool = False
    requires_logoff: bool = False
    # Яке значення перемикача показувати, якщо значення реєстру відсутнє
    # (типовий стан Windows "з коробки" для цього твіка).
    missing_state: bool = False


TWEAKS: tuple[Tweak, ...] = (
    # ------------------------------------------------------------- ІГРИ
    Tweak(
        id="game_dvr",
        title="Xbox Game DVR / фоновий запис",
        description=(
            "Вимикає фоновий запис геймплею через Xbox Game Bar — він може забирати "
            "ресурси процесора й диска під час гри."
        ),
        risk=RISK_SAFE,
        group=GROUP_GAMES,
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
        id="game_mode",
        title="Game Mode Windows",
        description=(
            "Вбудований режим Windows, який надає грі пріоритет над фоновими "
            "процесами й оновленнями під час запуску."
        ),
        risk=RISK_SAFE,
        group=GROUP_GAMES,
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
        id="fullscreen_opt_off",
        title="Повноекранні оптимізації (глобально)",
        description=(
            "Вимикає системну обробку повноекранного режиму Windows одразу для "
            "всіх ігор — у деяких іграх це дає стабільнішу частоту кадрів у "
            "справжньому повноекранному режимі."
        ),
        risk=RISK_CAUTION,
        group=GROUP_GAMES,
        entries=(
            RegEntry(
                winreg.HKEY_CURRENT_USER, r"System\GameConfigStore",
                "GameDVR_FSEBehaviorMode", "dword", 2, 0,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER, r"System\GameConfigStore",
                "GameDVR_HonorUserFSEBehaviorMode", "dword", 1, 0,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER, r"System\GameConfigStore",
                "GameDVR_DXGIHonorFSEWindowsCompatible", "dword", 1, 0,
            ),
        ),
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
        group=GROUP_GAMES,
        entries=(
            RegEntry(
                winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
                "HwSchMode", "dword", 2, 1,
            ),
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
        group=GROUP_GAMES,
        entries=(
            RegEntry(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games",
                "GPU Priority", "dword", 8, 8,
            ),
            RegEntry(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games",
                "Priority", "dword", 6, 2,
            ),
            RegEntry(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games",
                "Scheduling Category", "sz", "High", "Medium",
            ),
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
        group=GROUP_GAMES,
        entries=(
            RegEntry(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia",
                "SystemResponsiveness", "dword", 10, 20,
            ),
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
        group=GROUP_INPUT,
        entries=(
            RegEntry(winreg.HKEY_CURRENT_USER, r"Control Panel\Mouse", "MouseSpeed", "sz", "0", "1"),
            RegEntry(winreg.HKEY_CURRENT_USER, r"Control Panel\Mouse", "MouseThreshold1", "sz", "0", "6"),
            RegEntry(winreg.HKEY_CURRENT_USER, r"Control Panel\Mouse", "MouseThreshold2", "sz", "0", "10"),
        ),
        requires_logoff=True,
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
        group=GROUP_INPUT,
        entries=(
            RegEntry(
                winreg.HKEY_CURRENT_USER, r"Control Panel\Accessibility\StickyKeys",
                "Flags", "sz", "506", "510",
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER, r"Control Panel\Accessibility\Keyboard Response",
                "Flags", "sz", "122", "126",
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER, r"Control Panel\Accessibility\ToggleKeys",
                "Flags", "sz", "58", "62",
            ),
        ),
    ),
    # -------------------------------------- ВИГЛЯД ("максимальна швидкодія")
    Tweak(
        id="transparency",
        title="Прозорість інтерфейсу",
        description="Вимикає ефект прозорості вікон, меню «Пуск» і панелі завдань.",
        risk=RISK_SAFE,
        group=GROUP_APPEARANCE,
        entries=(
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                "EnableTransparency", "dword", 0, 1,
            ),
        ),
    ),
    Tweak(
        id="window_menu_anim",
        title="Анімації вікон і меню",
        description="Вимикає анімацію згортання/розгортання вікон і появи меню.",
        risk=RISK_SAFE,
        group=GROUP_APPEARANCE,
        entries=(
            RegEntry(
                winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop\WindowMetrics",
                "MinAnimate", "sz", "0", "1",
            ),
            RegEntry(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", "MenuAnimation", "sz", "0", "1"),
        ),
        requires_logoff=True,
    ),
    Tweak(
        id="shadows_taskbar_anim",
        title="Тіні, згладжування й анімація панелі завдань",
        description=(
            "Вимикає тінь під підписами значків, прозоре виділення в списках і "
            "анімацію кнопок панелі завдань."
        ),
        risk=RISK_SAFE,
        group=GROUP_APPEARANCE,
        entries=(
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "ListviewShadow", "dword", 0, 1,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "ListviewAlphaSelect", "dword", 0, 1,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "TaskbarAnimations", "dword", 0, 1,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM",
                "EnableAeroPeek", "dword", 0, 1,
            ),
        ),
        requires_logoff=True,
    ),
    Tweak(
        id="menu_show_delay",
        title="Затримка показу меню",
        description="Прибирає паузу перед розгортанням підменю (MenuShowDelay = 0).",
        risk=RISK_SAFE,
        group=GROUP_APPEARANCE,
        entries=(
            RegEntry(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", "MenuShowDelay", "sz", "0", "400"),
        ),
    ),
    Tweak(
        id="visual_fx_performance",
        title="Візуальні ефекти «найкраща швидкодія»",
        description=(
            "Перемикає загальний пресет візуальних ефектів Windows на «Забезпечити "
            "найкращу швидкодію» в параметрах швидкодії системи."
        ),
        risk=RISK_SAFE,
        group=GROUP_APPEARANCE,
        entries=(
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects",
                "VisualFXSetting", "dword", 2, 0,
            ),
            RegEntry(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", "DragFullWindows", "sz", "0", "1"),
        ),
        requires_logoff=True,
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
        group=GROUP_PRIVACY,
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
        id="advertising_id",
        title="Рекламний ідентифікатор",
        description=(
            "Забороняє застосункам використовувати рекламний ідентифікатор для "
            "персоналізованої реклами."
        ),
        risk=RISK_SAFE,
        group=GROUP_PRIVACY,
        entries=(
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo",
                "Enabled", "dword", 0, 1,
            ),
        ),
    ),
    Tweak(
        id="bing_search",
        title="Пошук Bing у меню «Пуск»",
        description="Вимикає веб-результати Bing при пошуку через меню «Пуск».",
        risk=RISK_SAFE,
        group=GROUP_PRIVACY,
        entries=(
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Search",
                "BingSearchEnabled", "dword", 0, 1,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Policies\Microsoft\Windows\Explorer",
                "DisableSearchBoxSuggestions", "dword", 1, 0,
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
        group=GROUP_PRIVACY,
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
        id="telemetry",
        title="Телеметрія Windows",
        description=(
            "Знижує обсяг діагностичних даних, які Windows надсилає Microsoft, до "
            "мінімального рівня. На Windows Home/Pro може не вимкнути збір даних "
            "повністю."
        ),
        risk=RISK_CAUTION,
        group=GROUP_PRIVACY,
        entries=(
            RegEntry(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\DataCollection",
                "AllowTelemetry", "dword", 0, 1,
            ),
        ),
    ),
    # ------------------------------------------------------------ СИСТЕМА
    Tweak(
        id="show_hidden_ext",
        title="Розширення файлів і приховані файли",
        description="Показує розширення файлів і приховані файли та папки в Провіднику.",
        risk=RISK_SAFE,
        group=GROUP_SYSTEM,
        entries=(
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "HideFileExt", "dword", 0, 1,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "Hidden", "dword", 1, 2,
            ),
        ),
        requires_logoff=True,
    ),
    Tweak(
        id="no_auto_suggested_apps",
        title="Автоматичне встановлення рекомендованих застосунків",
        description="Забороняє Windows самостійно встановлювати застосунки, які вона «рекомендує».",
        risk=RISK_SAFE,
        group=GROUP_SYSTEM,
        entries=(
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
                "SilentInstalledAppsEnabled", "dword", 0, 1,
            ),
            RegEntry(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
                "PreInstalledAppsEnabled", "dword", 0, 1,
            ),
        ),
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
    data = load_data()

    initial_state = dict(data.get("registry_tweaks_initial_state", {}))
    if tweak.id not in initial_state:
        initial_state[tweak.id] = get_state(tweak)
        update_data("registry_tweaks_initial_state", initial_state)

    if not data.get("registry_tweaks_backup_done", False):
        _run_full_backup()
        update_data("registry_tweaks_backup_done", True)


def has_initial_state() -> bool:
    return bool(load_data().get("registry_tweaks_initial_state"))


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
    data = load_data()
    initial_state = data.get("registry_tweaks_initial_state", {})

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


# ------------------------------------------------------ режим Windows (вигляд)

def apply_max_performance() -> list[tuple[Tweak, bool, str]]:
    """Вмикає всі твіки розділу "Вигляд" (кнопка «Максимальна швидкодія")."""
    results = []
    for tweak in get_appearance_tweaks():
        if get_state(tweak):
            continue
        success, error = set_tweak(tweak, True)
        results.append((tweak, success, error))
    return results


def restore_appearance_defaults() -> list[tuple[Tweak, bool, str]]:
    """Повертає твіки розділу "Вигляд" до збережених початкових значень
    (кнопка «Повернути гарну Windows»). Якщо твік ще не мав збереженого
    початкового стану — вважається, що типовий стан Windows вимкнений."""
    data = load_data()
    initial_state = data.get("registry_tweaks_initial_state", {})

    results = []
    for tweak in get_appearance_tweaks():
        target = bool(initial_state.get(tweak.id, False))
        if get_state(tweak) == target:
            continue
        success, error = set_tweak(tweak, target)
        results.append((tweak, success, error))
    return results


def restart_explorer() -> bool:
    """Перезапускає Провідник, щоб зміни вигляду (панель завдань, меню) набули
    чинності без повного виходу з системи. Закриває відкриті вікна папок."""
    try:
        subprocess.run(
            ["taskkill", "/f", "/im", "explorer.exe"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, creationflags=_NO_WINDOW,
        )
        subprocess.Popen(["explorer.exe"])
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        _logger.error("Не вдалося перезапустити Провідник: %s", exc)
        return False
