"""Логіка вкладки «Ігровий режим»: профілі, керування процесами та планами живлення."""

import os
import re
import subprocess

import psutil

from core.app_data import load_data, update_data
from core.system_processes import is_hidden, is_protected

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_CURRENT_PID = os.getpid()

POWER_PLANS = {
    "Збалансований": "381b4222-f694-41f0-9685-ff5bb260df2e",
    "Висока продуктивність": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
    "Економія енергії": "a1841308-3541-4fab-bc81-f71556f20b4a",
}
HIGH_PERFORMANCE_GUID = POWER_PLANS["Висока продуктивність"]

DEFAULT_PROFILES = {
    "Гра": {"processes": [], "power_plan": HIGH_PERFORMANCE_GUID},
    "Стрім": {"processes": [], "power_plan": HIGH_PERFORMANCE_GUID},
    "Робота": {"processes": [], "power_plan": POWER_PLANS["Збалансований"]},
}

DEFAULT_GAME_MODE = {
    "profiles": DEFAULT_PROFILES,
    "active_profile": "Гра",
    "games": [],
    "previous_power_plan": None,
    "is_active": False,
}


def load_game_mode() -> dict:
    """Завантажує стан ігрового режиму з data.json, доповнюючи типовими профілями."""
    data = load_data()
    saved = data.get("game_mode", {})

    profiles = {name: dict(defaults) for name, defaults in DEFAULT_PROFILES.items()}
    for name, profile in saved.get("profiles", {}).items():
        if name in profiles:
            profiles[name].update(profile)
        else:
            profiles[name] = profile

    return {
        "profiles": profiles,
        "active_profile": saved.get("active_profile", DEFAULT_GAME_MODE["active_profile"]),
        "games": list(saved.get("games", [])),
        "previous_power_plan": saved.get("previous_power_plan"),
        "is_active": saved.get("is_active", False),
    }


def save_game_mode(state: dict) -> None:
    update_data("game_mode", state)


def get_running_process_names() -> list[dict]:
    """Унікальні (за назвою) запущені процеси, без прихованих. name/count/protected."""
    counts = {}
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

    result = [
        {"name": name, "count": count, "protected": is_protected(name)}
        for name, count in counts.items()
    ]
    result.sort(key=lambda p: p["name"].lower())
    return result


def get_running_process_name_set() -> set[str]:
    """Множина назв (у нижньому регістрі) усіх запущених процесів, окрім поточного."""
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


def terminate_by_names(names: list[str]) -> list[tuple[str, bool, str]]:
    """Завершує запущені процеси з переданими назвами (крім прихованих/захищених/себе)."""
    targets = {n.lower() for n in names}
    results = []

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            info = proc.info
            pid = info["pid"]
            name = info["name"] or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

        if pid == _CURRENT_PID or name.lower() not in targets:
            continue
        if is_hidden(name) or is_protected(name):
            continue

        try:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except psutil.TimeoutExpired:
                proc.kill()
            results.append((name, True, ""))
        except psutil.NoSuchProcess:
            results.append((name, True, ""))
        except psutil.AccessDenied:
            results.append((name, False, "Немає прав для завершення цього процесу"))
        except Exception as exc:
            results.append((name, False, str(exc)))

    return results


def get_active_power_scheme() -> str | None:
    """GUID активного плану живлення, або None, якщо не вдалося визначити."""
    try:
        raw = subprocess.check_output(
            ["powercfg", "/getactivescheme"],
            stderr=subprocess.STDOUT,
            timeout=3,
            creationflags=_NO_WINDOW,
        ).decode("utf-8", errors="ignore")
    except (subprocess.SubprocessError, OSError):
        return None

    match = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F-]{27})", raw)
    return match.group(1).lower() if match else None


def set_active_power_scheme(guid: str) -> tuple[bool, str]:
    if not guid:
        return True, ""

    try:
        result = subprocess.run(
            ["powercfg", "/setactive", guid],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
            creationflags=_NO_WINDOW,
        )
        if result.returncode != 0:
            return False, result.stderr.decode("utf-8", errors="ignore").strip()
        return True, ""
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)


def power_plan_name(guid: str | None) -> str:
    for name, plan_guid in POWER_PLANS.items():
        if guid and plan_guid.lower() == guid.lower():
            return name
    return "Без змін" if not guid else "Інший план"


def activate_profile(state: dict, profile_name: str) -> tuple[dict, list[tuple[str, bool, str]]]:
    """Закриває позначені процеси профілю та вмикає його план живлення."""
    profile = state["profiles"].get(profile_name, {"processes": [], "power_plan": ""})

    terminate_results = terminate_by_names(profile.get("processes", []))

    previous_guid = get_active_power_scheme()
    target_guid = profile.get("power_plan") or ""
    if target_guid:
        set_active_power_scheme(target_guid)

    state["previous_power_plan"] = previous_guid
    state["is_active"] = True
    state["active_profile"] = profile_name
    save_game_mode(state)

    return state, terminate_results


def deactivate_profile(state: dict) -> dict:
    """Повертає попередній план живлення, збережений під час активації."""
    previous_guid = state.get("previous_power_plan")
    if previous_guid:
        set_active_power_scheme(previous_guid)

    state["is_active"] = False
    state["previous_power_plan"] = None
    save_game_mode(state)

    return state
