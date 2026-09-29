"""Єдине місце в PulseFPS, де дозволено завершувати процеси.

ПРАВИЛО ПРОЄКТУ (перевіряє tools/check_safety.py):
  * .terminate() / .kill() / taskkill / TerminateProcess викликаються ЛИШЕ тут;
  * кожна функція цього модуля вимагає UserAction — доказ, що користувач сам
    натиснув кнопку і підтвердив дію в діалозі. UserAction створює тільки
    ask_user_action() — і лише в головному (UI) потоці, тобто фонові потоки
    (сканування, стеження за іграми, автоувімкнення) отримати його не можуть;
  * сканування — тільки читання: ні завершення процесів, ні видалення файлів;
  * кожне завершення пишеться в logs.txt (get_audit_logger) з причиною й часом.
"""

import subprocess
import threading

import psutil

from core.logging_setup import get_audit_logger
from core.system_processes import is_protected

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class UserAction:
    """Підтверджена користувачем дія. reason — що саме натиснуто (для журналу)."""

    __slots__ = ("reason",)
    _issuing = False

    def __init__(self, reason: str):
        if not UserAction._issuing:
            raise PermissionError("UserAction створюється лише через ask_user_action()")
        self.reason = reason

    def __repr__(self) -> str:
        return f"UserAction({self.reason!r})"


def ask_user_action(parent, title: str, message: str, reason: str, icon: str = "question") -> UserAction | None:
    """Показує підтвердження; «Так» -> UserAction, інакше None.
    Викликати тільки з обробника натискання кнопки (головний потік Tk)."""
    if threading.current_thread() is not threading.main_thread():
        get_audit_logger().error("Відмовлено: запит підтвердження з фонового потоку (%s)", reason)
        return None
    from tkinter import messagebox
    if not messagebox.askyesno(title, message, icon=icon, parent=parent):
        get_audit_logger().info("Скасовано користувачем: %s", reason)
        return None
    UserAction._issuing = True
    try:
        action = UserAction(reason)
    finally:
        UserAction._issuing = False
    get_audit_logger().info("Підтверджено користувачем: %s", reason)
    return action


def require(action, what: str) -> None:
    """Кидає PermissionError (і пише в журнал), якщо дію не підтвердив користувач."""
    if not isinstance(action, UserAction):
        get_audit_logger().error("ЗАБЛОКОВАНО без підтвердження користувача: %s", what)
        raise PermissionError(f"{what}: потрібне підтвердження користувача")


def _describe(proc: psutil.Process) -> str:
    try:
        return f"{proc.name()} (PID {proc.pid})"
    except psutil.Error:
        return f"PID {proc.pid}"


def terminate_processes(targets, action: UserAction, graceful_command: list[str] | None = None,
                        timeout: float = 5.0) -> tuple[int, list[str]]:
    """Завершує процеси. targets — [(pid, create_time_unix|None)]; процес із тим самим
    PID, але іншим часом створення (PID перевикористано), не чіпаємо. Системні
    (is_protected) не завершуються ніколи. graceful_command — штатна команда
    виходу (напр. steam.exe -shutdown), яку спершу даємо виконати.
    -> (завершено, помилки)."""
    require(action, f"завершення процесів {[pid for pid, _ in targets]}")
    audit = get_audit_logger()

    procs, errors = [], []
    for pid, create_time in targets:
        if create_time and create_time > 1e12:  # FILETIME (100 нс від 1601) зі знімка Монітора
            create_time = create_time / 1e7 - 11644473600.0
        try:
            proc = psutil.Process(pid)
            if create_time and abs(proc.create_time() - create_time) > 1.0:
                continue
            if is_protected(proc.name()):
                audit.error("Пропущено системний процес %s — причина: %s", _describe(proc), action.reason)
                continue
            procs.append(proc)
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as exc:
            errors.append(f"PID {pid}: {exc}")

    if graceful_command and procs:
        audit.info("Штатний вихід: %s — причина: %s", " ".join(graceful_command), action.reason)
        try:
            subprocess.Popen(graceful_command, creationflags=_NO_WINDOW)
            _gone, procs = psutil.wait_procs(procs, timeout=10)
        except (OSError, psutil.Error) as exc:
            audit.error("Штатний вихід не вдався: %s", exc)

    names = {proc.pid: _describe(proc) for proc in procs}
    for proc in procs:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            errors.append(f"{names[proc.pid]}: немає прав для завершення")
        except psutil.Error as exc:
            errors.append(f"{names[proc.pid]}: {exc}")
    _gone, alive = psutil.wait_procs(procs, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            pass
    _gone, alive = psutil.wait_procs(alive, timeout=3)

    alive_pids = {proc.pid for proc in alive}
    killed = 0
    for proc in procs:
        if proc.pid in alive_pids:
            audit.error("НЕ завершено %s — причина: %s", names[proc.pid], action.reason)
        else:
            killed += 1
            audit.info("Завершено процес %s — причина: %s", names[proc.pid], action.reason)
    return killed, errors


def find_by_names(names) -> list[tuple[int, float]]:
    """(pid, create_time) запущених процесів із такими назвами exe — лише читання."""
    wanted = {n.lower() for n in names}
    found = []
    for proc in psutil.process_iter(["pid", "name", "create_time"]):
        try:
            if (proc.info["name"] or "").lower() in wanted:
                found.append((proc.info["pid"], proc.info["create_time"]))
        except psutil.Error:
            continue
    return found


def restart_explorer(action: UserAction) -> bool:
    """Перезапускає Провідник (після зміни твіків вигляду)."""
    require(action, "перезапуск Провідника")
    audit = get_audit_logger()
    try:
        subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=10, creationflags=_NO_WINDOW)
        audit.info("Завершено процес explorer.exe (перезапуск) — причина: %s", action.reason)
        subprocess.Popen(["explorer.exe"])
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        audit.error("Не вдалося перезапустити Провідник: %s", exc)
        return False
