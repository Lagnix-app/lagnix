"""Перезапуск Lagnix (зміна мови, як у Steam/Discord): новий екземпляр стартує з уже
підвищеного процесу — тих самих прав без другого вікна UAC.

Потік: старий процес зберігає вкладку/прокрутку/геометрію (data.json, `restart_state`) і
запускає `<exe> [main.py] --restarted <pid>`, після чого закривається. Новий чекає, доки
старий справді завершиться (mutex одного екземпляра, трей і гарячі клавіші звільняться),
відновлює стан і НЕ скидає Ігровий режим (`is_restarted()`): його стан лежить у data.json.
У зібраному Lagnix.exe (PyInstaller) `sys.executable` — сам exe, тож команда така:
`Lagnix.exe --restarted <pid>`; для `python main.py` — `pythonw.exe main.py --restarted <pid>`."""

import os
import subprocess
import sys
import time

import psutil

from core.app_data import load_data, update_data
from core.logging_setup import get_audit_logger, get_logger

FLAG = "--restarted"
_STATE_KEY = "restart_state"
_STATE_MAX_AGE_S = 120
_WAIT_PREVIOUS_S = 20
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_log = get_logger("core.restart")


def previous_pid(argv: list[str] | None = None) -> int | None:
    """PID старого екземпляра, якщо цей запуск — перезапуск (`--restarted <pid>`)."""
    argv = sys.argv[1:] if argv is None else argv
    if FLAG not in argv:
        return None
    try:
        return int(argv[argv.index(FLAG) + 1])
    except (IndexError, ValueError):
        return 0  # прапор є, PID зіпсований — все одно «перезапуск», чекати нічого


def is_restarted() -> bool:
    return previous_pid() is not None


def wait_for_previous() -> None:
    """Чекає завершення старого екземпляра (до _WAIT_PREVIOUS_S): інакше mutex одного
    екземпляра відштовхнув би новий запуск."""
    pid = previous_pid()
    if not pid or pid == os.getpid():
        return
    deadline = time.monotonic() + _WAIT_PREVIOUS_S
    try:
        proc = psutil.Process(pid)
    except psutil.Error:
        return
    try:
        proc.wait(timeout=_WAIT_PREVIOUS_S)
    except psutil.TimeoutExpired:
        _log.error("The previous Lagnix instance (PID %s) did not exit within %s s", pid, _WAIT_PREVIOUS_S)
    except psutil.Error:
        pass
    # PID міг звільнитись раніше, ніж ОС закрила всі дескриптори (mutex) — короткий запас
    time.sleep(max(0.0, min(0.3, deadline - time.monotonic())))


def _command() -> list[str]:
    args, skip = [], False
    for arg in sys.argv[1:]:  # --minimized (автозапуск) не переносимо; --restarted <pid> — теж
        if skip:
            skip = False
        elif arg == FLAG:
            skip = True
        elif arg != "--minimized":
            args.append(arg)
    if getattr(sys, "frozen", False):  # PyInstaller: sys.executable — Lagnix.exe
        return [sys.executable, *args, FLAG, str(os.getpid())]
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pythonw):
        exe = pythonw  # без консольного вікна
    return [exe, os.path.join(_ROOT, "main.py"), *args, FLAG, str(os.getpid())]


def save_state(tab: str | None, scroll: float, geometry: str | None) -> None:
    update_data(_STATE_KEY, {"tab": tab, "scroll": scroll, "geometry": geometry, "time": time.time()})


def pop_state() -> dict | None:
    """Стан, збережений перед перезапуском (лише для запуску з --restarted і не старший за 2 хв)."""
    if not is_restarted():
        return None
    state = load_data().get(_STATE_KEY)
    update_data(_STATE_KEY, None)
    if not isinstance(state, dict) or time.time() - float(state.get("time", 0)) > _STATE_MAX_AGE_S:
        return None
    return state


def spawn_new_instance() -> None:
    """Запускає новий екземпляр (OSError — наверх, викликач покаже помилку й лишиться працювати)."""
    command = _command()
    get_audit_logger().info("Restarting Lagnix: %s", " ".join(command))
    cwd = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else _ROOT
    subprocess.Popen(command, cwd=cwd, close_fds=True, creationflags=_NEW_GROUP | _NO_WINDOW)
