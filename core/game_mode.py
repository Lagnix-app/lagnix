"""Логіка «Ігрового режиму»: закриття фонових програм, план живлення, стан у data.json.

Що робить увімкнення:
  1. ЛИШЕ при ручному увімкненні з підтвердженням — закриває показаний
     користувачу розумний список фонових програм (core/smart_apps.py) і
     процеси профілю («Розширені»); автоувімкнення (гра запустилась)
     нічого не закриває;
  2. запам'ятовує поточний план живлення й вмикає «PulseFPS Ultra»
     (або інший, обраний у профілі);
  3. запам'ятовує закриті програми, щоб при вимкненні запропонувати
     відкрити саме їх.
Вимкнення повертає збережений план. Функції activate/deactivate лише змінюють
переданий state — записує його на диск викликач. Прапорець is_active зберігається на
диску: якщо PulseFPS аварійно закрився під час режиму, при наступному
запуску вкладка запропонує повернути попередній план.
"""

import copy
import os

import psutil

from core import app_catalog, power_plans, process_control, process_snapshot, smart_apps
from core.app_data import load_data, update_data
from core.i18n import has_key, t
from core.logging_setup import get_logger
from core.system_processes import is_hidden, is_protected

_logger = get_logger(__name__)
_CURRENT_PID = os.getpid()

# id плану -> GUID; назва для показу — power_plan_name() (переклад "power_plan.<id>")
POWER_PLANS = {
    "balanced": power_plans.BALANCED_GUID,
    "high_performance": power_plans.HIGH_PERFORMANCE_GUID,
    "power_saver": "a1841308-3541-4fab-bc81-f71556f20b4a",
}
HIGH_PERFORMANCE_GUID = power_plans.HIGH_PERFORMANCE_GUID
POWER_SAVER_GUID = POWER_PLANS["power_saver"]

# У профілі план живлення — GUID, порожній рядок ("Без змін") або цей маркер:
# «PulseFPS Ultra», який створюється при першому вмиканні.
ULTRA = "ultra"
SCHEMA_VERSION = 4

# id профілю -> типові значення; назва для показу — profile_name() (переклад "game_mode.profile.<id>")
DEFAULT_PROFILES = {
    "game": {"processes": [], "power_plan": ULTRA},
    "stream": {"processes": [], "power_plan": ULTRA},
    "work": {"processes": [], "power_plan": POWER_PLANS["balanced"]},
}
PROFILE_IDS = tuple(DEFAULT_PROFILES)

# Схема < 4 зберігала профілі в data.json під українськими назвами — це не
# текст інтерфейсу, а старі ключі даних, потрібні лише для міграції.
_LEGACY_PROFILE_IDS = {"Гра": "game", "Стрім": "stream", "Робота": "work"}

DEFAULT_GAME_MODE = {
    "schema": SCHEMA_VERSION,
    "profiles": DEFAULT_PROFILES,
    "active_profile": "game",
    "games": [],            # вручну додані exe (автоперемикання, як раніше)
    "auto_games": [],       # ключі знайдених ігор із увімкненим автоперемиканням
    "excluded_apps": [],    # застаріле (схема < 3): переноситься в app_choices
    "levels": {},           # профіль -> рівень: soft / balanced / max (core/app_catalog.py)
    "app_choices": {},      # профіль -> рівень -> {exe: True «закривати» / False «не закривати»}
    "user_apps": {},        # exe -> {title, exe_path}: програми, додані вручну через «+ Додати програму»
    "never_close": None,    # None = типовий список app_catalog.DEFAULT_NEVER_CLOSE, інакше — список користувача
    "collapsed_groups": [], # згорнуті групи в блоці «Фонові програми»
    "closed_apps": [],      # [{title, name, exe_path, memory_mb}] — закриті під час режиму
    "freed_mb": 0,
    "plan_name": None,      # який план увімкнено режимом (назва на момент увімкнення; застаріле)
    "plan_value": None,     # той самий план: маркер Ultra / GUID — назва поточною мовою через power_plan_name()
    "ultra_guid": None,
    "previous_power_plan": None,
    "is_active": False,
}


def load_game_mode() -> dict:
    """Стан із data.json, доповнений типовими значеннями (і мігрований зі старої схеми)."""
    saved = _migrate_profile_ids(load_data().get("game_mode", {}))

    profiles = {name: dict(defaults) for name, defaults in DEFAULT_PROFILES.items()}
    for name, profile in saved.get("profiles", {}).items():
        if name in profiles:
            profiles[name].update(profile)
        else:
            profiles[name] = profile

    if saved.get("schema", 1) < SCHEMA_VERSION:
        # раніше типовим планом «Гри» й «Стріму» була «Висока продуктивність»
        for name in ("game", "stream"):
            if profiles[name].get("power_plan") == HIGH_PERFORMANCE_GUID:
                profiles[name]["power_plan"] = ULTRA

    state = copy.deepcopy(DEFAULT_GAME_MODE)  # глибока копія: словники стану не мають ділитися з типовими
    for key in state:
        if key in saved:
            state[key] = saved[key]
    state["profiles"] = profiles
    if saved.get("schema", 1) < 3 and state.get("excluded_apps"):
        # раніше вилучені зі списку програми = «не закривати» на всіх рівнях усіх профілів
        for name in profiles:
            for level in app_catalog.LEVELS:
                bucket = state["app_choices"].setdefault(name, {}).setdefault(level, {})
                for exe in state["excluded_apps"]:
                    bucket.setdefault(exe.lower(), False)
        state["excluded_apps"] = []
    state["schema"] = SCHEMA_VERSION
    return state


def _migrate_profile_ids(saved: dict) -> dict:
    """Схема < 4: українські назви профілів як ключі -> id (game / stream / work)."""
    if saved.get("schema", 1) >= 4:
        return saved
    saved = copy.deepcopy(saved)

    def rename(mapping):
        if not isinstance(mapping, dict):
            return mapping
        return {_LEGACY_PROFILE_IDS.get(k, k): v for k, v in mapping.items()}

    for key in ("profiles", "levels", "app_choices"):
        if key in saved:
            saved[key] = rename(saved[key])
    if saved.get("active_profile") in _LEGACY_PROFILE_IDS:
        saved["active_profile"] = _LEGACY_PROFILE_IDS[saved["active_profile"]]
    return saved


def profile_name(profile_id: str) -> str:
    """Назва профілю поточною мовою (невідомий id — як є)."""
    key = f"game_mode.profile.{profile_id}"
    return t(key) if has_key(key) else profile_id


def default_level() -> str:
    """Рівень для профілів, де його ще не обирали — з «Налаштування → Ігровий режим»."""
    from core.settings import load_settings
    level = load_settings().get("game_mode_default_level")
    return level if level in app_catalog.LEVELS else app_catalog.DEFAULT_LEVEL


def level_of(state: dict, profile_name: str) -> str:
    level = state.get("levels", {}).get(profile_name)
    return level if level in app_catalog.LEVELS else default_level()


def choices_of(state: dict, profile_name: str, level: str) -> dict[str, bool]:
    return state.get("app_choices", {}).get(profile_name, {}).get(level, {})


def never_close_of(state: dict) -> list[str]:
    patterns = state.get("never_close")
    return list(app_catalog.DEFAULT_NEVER_CLOSE) if patterns is None else list(patterns)


def save_game_mode(state: dict) -> None:
    update_data("game_mode", state)


# --------------------------------------------------------------- процеси

def get_running_process_names() -> list[dict]:
    """Унікальні (за назвою) запущені процеси, без прихованих. name/count/protected/pid
    (pid — першого знайденого екземпляра, щоб показати шлях до exe в підказці)."""
    counts = {}
    first_pid = {}
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            info = proc.info
            pid = info["pid"]
            name = info["name"] or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

        if pid == _CURRENT_PID or not name or is_hidden(name):
            continue

        counts[name] = counts.get(name, 0) + 1
        first_pid.setdefault(name, pid)

    result = [
        {"name": name, "count": count, "protected": is_protected(name), "pid": first_pid[name]}
        for name, count in counts.items()
    ]
    result.sort(key=lambda p: p["name"].lower())
    return result


def get_running_process_name_set() -> set[str]:
    """Множина назв (у нижньому регістрі) усіх запущених процесів, окрім поточного.
    Швидкий шлях — один системний виклик без CPU-стану (~3 мс), psutil — запасний."""
    if process_snapshot.is_available():
        try:
            return {p["name"].lower() for p in process_snapshot.sample(track_cpu=False)
                    if p["pid"] != _CURRENT_PID and p["name"] != "—"}
        except OSError:
            pass
    names = set()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            info = proc.info
            if info["pid"] == _CURRENT_PID:
                continue
            name = info["name"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

        if name:
            names.add(name.lower())

    return names


def find_game_process(exe_names, folder: str | None) -> dict | None:
    """Запущений процес гри (лише читання): назва exe з exe_names І, якщо тека гри
    відома, exe лежить у цій теці — щоб чужий процес з тією ж назвою (fr.exe,
    tf.exe…) не вмикав режим. -> {"pid", "name", "exe"} або None."""
    names = {n.lower() for n in exe_names}
    root = os.path.normcase(os.path.normpath(folder)) + os.sep if folder else None
    for proc in psutil.process_iter(["pid", "name", "exe"], ad_value=None):
        info = proc.info
        name = (info.get("name") or "").lower()
        if name not in names or info["pid"] == _CURRENT_PID:
            continue
        exe = info.get("exe") or ""
        if root is not None and not os.path.normcase(exe).startswith(root):
            continue
        return {"pid": info["pid"], "name": info.get("name"), "exe": exe or None}
    return None


# ---------------------------------------------------------- плани живлення

def get_active_power_scheme() -> str | None:
    """GUID активного плану живлення, або None, якщо не вдалося визначити."""
    return power_plans.get_active_scheme()


def set_active_power_scheme(guid: str) -> tuple[bool, str]:
    return power_plans.set_active_scheme(guid)


def power_plan_name(guid: str | None) -> str:
    """Назва плану для показу: Ultra, стандартні, «Без змін» або «Інший план»."""
    if guid == ULTRA:
        return power_plans.ULTRA_NAME
    for plan_id, plan_guid in POWER_PLANS.items():
        if guid and plan_guid.lower() == guid.lower():
            return t(f"power_plan.{plan_id}")
    return t("power_plan.unchanged") if not guid else t("power_plan.other")


def plan_choices() -> dict[str, str]:
    """Назва (поточною мовою) -> значення профілю (маркер Ultra / GUID / "" для «Без змін»)."""
    choices = {power_plans.ULTRA_NAME: ULTRA}
    for plan_id, plan_guid in POWER_PLANS.items():
        choices[t(f"power_plan.{plan_id}")] = plan_guid
    choices[t("power_plan.unchanged")] = ""
    return choices


def _resolve_plan(state: dict, plan: str, auto: bool) -> tuple[str | None, str | None, str]:
    """(GUID для увімкнення або None, назва, повідомлення). Ultra на батареї
    автоматично не вмикається."""
    if not plan:
        return None, None, ""
    if plan != ULTRA:
        return plan, power_plan_name(plan), ""
    if auto and power_plans.on_battery():
        return None, None, t("game_mode.on_battery_no_ultra")
    guid, error = power_plans.ensure_ultra(state.get("ultra_guid"))
    if guid:
        state["ultra_guid"] = guid
        return guid, power_plans.ULTRA_NAME, ""
    return None, None, error


# --------------------------------------------------- увімкнення/вимкнення

def activate(state: dict, profile_name: str, auto: bool = False, plan_override: str | None = None,
             action=None, apps: list[dict] | None = None, extras: list[str] | None = None) -> dict:
    """Вмикає режим, змінюючи `state` (зберігає його ВИКЛИКАЧ — операція довга,
    а стан читають кілька потоків). auto=True — режим вмикається сам (гра
    запустилась): «PulseFPS Ultra» на батареї тоді пропускається. plan_override —
    значення плану замість профільного ("" = не чіпати план).

    Програми закриваються ЛИШЕ з action (process_control.UserAction — користувач
    натиснув перемикач і підтвердив список) і лише ті, що були в показаному
    списку: apps (розумний список) та extras (назви процесів профілю).
    Автоувімкнення (action=None) нічого не закриває — тільки план живлення.
    -> звіт: closed [{title, name, exe_path, memory_mb}], freed_mb, errors, plan_name, plan_error."""
    profile = state["profiles"].get(profile_name, {"processes": [], "power_plan": ULTRA})

    closed, errors = [], []
    if action is not None:
        to_close = list(apps or [])
        for name in extras or []:  # процеси, позначені в профілі вручну, — теж м'яко
            targets = process_control.find_by_names([name])
            if targets:
                to_close.append({"title": name, "name": name, "exe_path": None, "memory_mb": 0,
                                 "targets": targets, "document": False})
        closed, errors = smart_apps.close_apps(to_close, action)

    if not state.get("is_active"):  # повторне вмикання не має затирати справжній «попередній» план
        current = power_plans.get_active_scheme()
        if current and current == (state.get("ultra_guid") or "").lower():
            current = power_plans.BALANCED_GUID  # Ultra лишився активним після збою — не «повертаємось» на нього
        state["previous_power_plan"] = current
    plan = profile.get("power_plan", "") if plan_override is None else plan_override
    guid, plan_name, plan_error = _resolve_plan(state, plan, auto)
    if guid:
        ok, message = power_plans.set_active_scheme(guid)
        if not ok:
            plan_name, plan_error = None, message or t("game_mode.err.switch_plan")

    state["is_active"] = True
    state["active_profile"] = profile_name
    state["closed_apps"] = [c for c in closed if c.get("exe_path")]
    state["freed_mb"] = sum(c.get("memory_mb", 0) for c in closed)
    state["plan_name"] = plan_name
    state["plan_value"] = (ULTRA if plan == ULTRA else guid) if plan_name else None
    return {"closed": closed, "freed_mb": state["freed_mb"], "errors": errors,
            "plan_name": plan_name, "plan_error": plan_error}


def deactivate(state: dict) -> list[dict]:
    """Вимикає режим, повертає попередній план (state зберігає виклик­ач).
    -> закриті програми для пропозиції «відкрити знову»."""
    previous = state.get("previous_power_plan")
    if previous:  # якщо план видалили — «Збалансований»
        power_plans.set_active_scheme(normal_plan_target(previous))

    closed = list(state.get("closed_apps", []))
    state["is_active"] = False
    state["previous_power_plan"] = None
    state["closed_apps"] = []
    state["freed_mb"] = 0
    state["plan_name"] = None
    state["plan_value"] = None
    return closed


def normal_plan_target(previous: str | None) -> str:
    """Куди повертати «звичайний» план: збережений попередній (якщо ще існує), інакше «Збалансований»."""
    return previous if previous and power_plans.scheme_exists(previous) else power_plans.BALANCED_GUID


def drop_ultra_from_profiles(state: dict) -> None:
    """Після видалення плану профілі, що на нього вказували, переходять на «Високу
    продуктивність» — щоб наступне увімкнення не створило його мовчки знову."""
    state["ultra_guid"] = None
    for profile in state["profiles"].values():
        if profile.get("power_plan") == ULTRA:
            profile["power_plan"] = HIGH_PERFORMANCE_GUID
