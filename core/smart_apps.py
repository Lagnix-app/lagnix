"""Розумний список фонових програм, які варто закрити перед грою.

Береться той самий знімок процесів, що й на «Моніторі», з групуванням у програми
(Edge з 40 процесів = одна програма). Що пропонувати — визначає каталог
core/app_catalog.py за рівнем («М'який» / «Збалансований» / «Максимальний») і
вибір користувача для поточного профілю й рівня (перемикач «закривати / не
закривати», додані вручну програми). НІКОЛИ не пропонуються:
  * системні процеси, античити, сам PulseFPS (жорстко, без винятків);
  * усе зі списку «Ніколи не закривати» (типовий — app_catalog.DEFAULT_NEVER_CLOSE,
    користувач його змінює);
  * лаунчер гри, що зараз запущена (Steam для Steam-гри, Riot Client для Valorant);
  * NVIDIA Overlay, якщо схоже, що йде запис.

Модуль лише ЧИТАЄ процеси. Закриття — close_apps() через
process_control.close_apps_gracefully (лише з підтвердженням користувача).
"""

from __future__ import annotations

import os
import subprocess
import time

import psutil

from core import app_catalog as catalog
from core import monitor as monitor_core
from core import process_control, process_info
from core.logging_setup import get_logger
from core.system_processes import is_hidden, is_protected

_logger = get_logger(__name__)
_CURRENT_PID = os.getpid()

CATEGORY_LABELS = catalog.CATEGORY_LABELS  # сумісність зі старим кодом


def is_hard_never(name: str) -> bool:
    """Системне, античит, сам PulseFPS — не закриваємо ніколи, незалежно від налаштувань."""
    low = name.lower()
    return (
        "pulsefps" in low
        or process_info.kind_for(name) in (process_info.ANTICHEAT, process_info.SYSTEM)
        or is_protected(name) or is_hidden(name)
    )


def matches_never(name: str, patterns) -> str | None:
    low = name.lower()
    return next((p for p in patterns if p and p in low), None)


def is_never_suggested(name: str, patterns=catalog.DEFAULT_NEVER_CLOSE) -> bool:
    return is_hard_never(name) or matches_never(name, patterns) is not None


def category_of(name: str) -> str | None:
    found = catalog.entry(name)
    return found[0] if found else None


class CpuSampler:
    """CPU % процесів між двома викликами (нормовано на всі ядра, як на «Моніторі»).
    Власний стан — не чіпає лічильники «Монітора». Перший замір дає 0."""

    def __init__(self):
        self._prev: dict[tuple[int, float | None], float] = {}
        self._prev_time = None
        self._cpus = psutil.cpu_count() or 1

    def sample(self, targets) -> dict[int, float]:
        now = time.monotonic()
        elapsed = (now - self._prev_time) if self._prev_time else None
        current, result = {}, {}
        for pid, create_time in targets:
            try:
                times = psutil.Process(pid).cpu_times()
            except psutil.Error:
                continue
            total = times.user + times.system
            key = (pid, create_time)
            current[key] = total
            if elapsed and key in self._prev:
                result[pid] = max(0.0, (total - self._prev[key]) / (elapsed * self._cpus) * 100.0)
        self._prev, self._prev_time = current, now
        return result


def _app_from_group(group: dict, members: list[dict], category: str, level: str | None,
                    platform: str | None, cpu: dict[int, float]) -> dict:
    return {
        "key": group["name"].lower(),
        "title": catalog.NICE_TITLES.get(group["name"].lower(), group["title"]),
        "name": group["name"],
        "exe_path": group.get("exe_path"),
        "category": category,
        "group": catalog.CATEGORY_GROUP.get(category, catalog.G_OTHER),
        "level": level,
        "platform": platform,
        "memory_mb": sum(m["memory_mb"] for m in members),
        "cpu_percent": sum(cpu.get(m["pid"], 0.0) for m in members),
        "count": len(members),
        "targets": [(m["pid"], m.get("create_time")) for m in members],
        "document": category in catalog.DOCUMENT_CATEGORIES,
        "warning": (catalog.PERIPHERAL_WARNING if category in ("peripheral", "rgb")
                    else catalog.OVERLAY_WARNING if category == "overlay" else None),
    }


def scan_running(groups: list[dict] | None = None, cpu_sampler: CpuSampler | None = None,
                 extra_names=()) -> list[dict]:
    """Усі запущені програми з каталогу (будь-якого рівня) + extra_names (додані
    користувачем), без жодної фільтрації за рівнем/вибором. Лише читання."""
    if groups is None:
        groups = monitor_core.get_process_groups()
    extra = {n.lower() for n in extra_names}
    picked = []
    for group in groups:
        root = group["name"]
        key = root.lower()
        found = catalog.entry(key)
        if found is None and key not in extra:
            continue
        if is_hard_never(root):
            continue
        members = [m for m in group["members"]
                   if m["pid"] != _CURRENT_PID and not is_protected(m["name"]) and not is_hard_never(m["name"])]
        if members:
            picked.append((group, members, found))
    targets = [(m["pid"], m.get("create_time")) for _g, members, _f in picked for m in members]
    cpu = cpu_sampler.sample(targets) if cpu_sampler else {}
    apps = []
    for group, members, found in picked:
        category, level, platform = found if found else ("user", None, None)
        apps.append(_app_from_group(group, members, category, level, platform, cpu))
    return apps


def plan_for_level(running: list[dict], level: str, choices: dict[str, bool], never_patterns,
                   protected_platforms: dict[str, str]) -> dict:
    """Що закривати на рівні level. choices — вибір користувача для профілю й рівня
    (exe -> True «закривати» / False «не закривати»). protected_platforms —
    платформа -> назва запущеної гри (її лаунчер не закриваємо).
    -> {"apps": [app + "close": bool], "skipped": [(title, причина)],
        "memory_mb", "cpu_percent"} — суми лише для тих, що буде закрито."""
    rank = catalog.LEVEL_RANK[level]
    apps, skipped = [], []
    for app in running:
        key = app["key"]
        never = matches_never(app["name"], never_patterns)
        if never is not None:
            continue  # показується у блоці «Ніколи не закривати»
        if app["platform"] and app["platform"] in protected_platforms:
            skipped.append((app["title"], catalog.TEXT_PLATFORM_PROTECTED.format(
                game=protected_platforms[app["platform"]])))
            continue
        if key in catalog.RECORDING_SENSITIVE and app["cpu_percent"] >= catalog.RECORDING_CPU_THRESHOLD:
            skipped.append((app["title"], catalog.TEXT_RECORDING))
            continue
        in_level = app["level"] is not None and catalog.LEVEL_RANK[app["level"]] <= rank
        choice = choices.get(key)
        if not in_level and choice is None:
            continue  # вищий рівень і користувач його сюди не додавав
        apps.append(dict(app, close=choice if choice is not None else True))
    apps.sort(key=lambda a: a["memory_mb"], reverse=True)
    closing = [a for a in apps if a["close"]]
    return {
        "apps": apps,
        "skipped": skipped,
        "memory_mb": sum(a["memory_mb"] for a in closing),
        "cpu_percent": sum(a["cpu_percent"] for a in closing),
    }


def compute_suggestions(excluded: set[str], game_platforms: set[str] = frozenset(),
                        groups: list[dict] | None = None) -> list[dict]:
    """Сумісність: програми рівня «Збалансований» без вилучених."""
    running = scan_running(groups)
    plan = plan_for_level(running, catalog.DEFAULT_LEVEL, {k: False for k in excluded},
                          catalog.DEFAULT_NEVER_CLOSE, {p: p for p in game_platforms})
    return [a for a in plan["apps"] if a["close"]]


def close_apps(apps: list[dict], action) -> tuple[list[dict], list[str]]:
    """Закриває програми, які користувач щойно підтвердив (action — process_control.UserAction):
    спершу м'яко (штатне закриття вікон), примусово — лише програми без вікон через 5 с.
    -> (закриті {title, name, exe_path, memory_mb}, повідомлення про незакриті)."""
    if not apps:
        return [], []
    try:
        results = process_control.close_apps_gracefully(apps, action)
    except Exception as exc:
        _logger.exception("Не вдалося закрити програми")
        return [], [str(exc)]
    closed, errors = [], []
    for app, result in zip(apps, results):
        if result["status"] in ("closed", "forced"):
            closed.append({"title": app["title"], "name": app["name"], "exe_path": app.get("exe_path"),
                           "memory_mb": app["memory_mb"]})
        else:
            errors.append(f"не вдалося закрити {app['title']}: {result['reason']}")
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
