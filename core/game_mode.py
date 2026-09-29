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

import os

import psutil

from core import power_plans, process_control, process_snapshot, smart_apps
from core.app_data import load_data, update_data
from core.logging_setup import get_logger
from core.system_processes import is_hidden, is_protected

_logger = get_logger(__name__)
_CURRENT_PID = os.getpid()

POWER_PLANS = {
    "Збалансований": power_plans.BALANCED_GUID,
    "Висока продуктивність": power_plans.HIGH_PERFORMANCE_GUID,
    "Економія енергії": "a1841308-3541-4fab-bc81-f71556f20b4a",
}
HIGH_PERFORMANCE_GUID = power_plans.HIGH_PERFORMANCE_GUID

# У профілі план живлення — GUID, порожній рядок ("Без змін") або цей маркер:
# «PulseFPS Ultra», який створюється при першому вмиканні.
ULTRA = "ultra"
SCHEMA_VERSION = 2

DEFAULT_PROFILES = {
    "Гра": {"processes": [], "power_plan": ULTRA},
    "Стрім": {"processes": [], "power_plan": ULTRA},
    "Робота": {"processes": [], "power_plan": POWER_PLANS["Збалансований"]},
}

DEFAULT_GAME_MODE = {
    "schema": SCHEMA_VERSION,
    "profiles": DEFAULT_PROFILES,
    "active_profile": "Гра",
    "games": [],            # вручну додані exe (автоперемикання, як раніше)
    "auto_games": [],       # ключі знайдених ігор із увімкненим автоперемиканням
    "excluded_apps": [],    # назви exe, які користувач прибрав із розумного списку
    "closed_apps": [],      # [{title, name, exe_path, memory_mb}] — закриті під час режиму
    "freed_mb": 0,
    "plan_name": None,      # який план увімкнено режимом (для показу)
    "ultra_guid": None,
    "previous_power_plan": None,
    "is_active": False,
}


def load_game_mode() -> dict:
    """Стан із data.json, доповнений типовими значеннями (і мігрований зі старої схеми)."""
    saved = load_data().get("game_mode", {})

    profiles = {name: dict(defaults) for name, defaults in DEFAULT_PROFILES.items()}
    for name, profile in saved.get("profiles", {}).items():
        if name in profiles:
            profiles[name].update(profile)
        else:
            profiles[name] = profile

    if saved.get("schema", 1) < SCHEMA_VERSION:
        # раніше типовим планом «Гри» й «Стріму» була «Висока продуктивність»
        for name in ("Гра", "Стрім"):
            if profiles[name].get("power_plan") == HIGH_PERFORMANCE_GUID:
                profiles[name]["power_plan"] = ULTRA

    state = {key: (list(v) if isinstance(v, list) else v) for key, v in DEFAULT_GAME_MODE.items()}
    for key in state:
        if key in saved:
            state[key] = saved[key]
    state["profiles"] = profiles
    state["schema"] = SCHEMA_VERSION
    return state


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
    for name, plan_guid in POWER_PLANS.items():
        if guid and plan_guid.lower() == guid.lower():
            return name
    return "Без змін" if not guid else "Інший план"


def plan_choices() -> dict[str, str]:
    """Назва -> значення профілю (маркер Ultra / GUID / "" для «Без змін»)."""
    choices = {power_plans.ULTRA_NAME: ULTRA}
    choices.update(POWER_PLANS)
    choices["Без змін"] = ""
    return choices


def _resolve_plan(state: dict, plan: str, auto: bool) -> tuple[str | None, str | None, str]:
    """(GUID для увімкнення або None, назва, повідомлення). Ultra на батареї
    автоматично не вмикається."""
    if not plan:
        return None, None, ""
    if plan != ULTRA:
        return plan, power_plan_name(plan), ""
    if auto and power_plans.on_battery():
        return None, None, "Ноутбук працює від батареї — «PulseFPS Ultra» автоматично не вмикається."
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
        closed, errors = smart_apps.close_apps(apps or [], action)
        for name in extras or []:
            killed, errs = process_control.terminate_processes(process_control.find_by_names([name]), action)
            errors.extend(f"{name}: {e}" for e in errs)
            if killed:
                closed.append({"title": name, "name": name, "exe_path": None, "memory_mb": 0})

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
            plan_name, plan_error = None, message or "Не вдалося переключити план живлення."

    state["is_active"] = True
    state["active_profile"] = profile_name
    state["closed_apps"] = [c for c in closed if c.get("exe_path")]
    state["freed_mb"] = sum(c.get("memory_mb", 0) for c in closed)
    state["plan_name"] = plan_name
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
