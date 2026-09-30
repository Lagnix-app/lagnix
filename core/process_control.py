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
import time

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


# ------------------------------------------------------------ м'яке закриття

_WM_CLOSE = 0x0010
_GW_OWNER = 4
_DIALOG_CLASS = "#32770"  # стандартний діалог Windows («Зберегти зміни?» тощо)


def _windows_of(pids: set[int]) -> list[dict]:
    """Верхньорівневі вікна процесів (лише читання): hwnd, pid, visible, cls, title."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            found.append({"hwnd": hwnd, "pid": pid.value, "visible": bool(user32.IsWindowVisible(hwnd)),
                          "owned": bool(user32.GetWindow(hwnd, _GW_OWNER)), "cls": cls.value,
                          "title": title.value})
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return found


def _main_windows(windows: list[dict]) -> list[dict]:
    """Видимі «справжні» вікна програми (не службові/приховані)."""
    return [w for w in windows if w["visible"] and (w["title"] or w["owned"])]


def _alive_pids(pids) -> set[int]:
    alive = set()
    for pid in pids:
        try:
            if psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                alive.add(pid)
        except psutil.Error:
            continue
    return alive


def close_apps_gracefully(apps: list[dict], action: UserAction, grace_s: float = 5.0) -> list[dict]:
    """Закриває програми «як користувач»: спершу WM_CLOSE усім їхнім вікнам
    (програма сама збереже стан або спитає «Зберегти?»). Через grace_s с:
      * процеси завершились — закрито штатно;
      * у програми лишилось видиме вікно (діалог «Зберегти?» чи просто не закрилась) —
        НЕ чіпаємо, повертаємо причину;
      * вікон немає (фонові/тray-програми, «залишки» браузера) — примусово через
        terminate_processes.
    apps: [{"title", "targets": [(pid, create_time)], "document": bool}].
    -> [{"title", "status": "closed"|"forced"|"kept"|"failed", "reason"}]."""
    require(action, f"закриття програм {[a.get('title') for a in apps]}")
    import ctypes

    audit = get_audit_logger()
    user32 = ctypes.windll.user32
    pending = []
    for app in apps:
        pids = {pid for pid, _ct in app["targets"]}
        windows = _windows_of(pids)
        had_windows = bool(_main_windows(windows))
        for window in windows:
            user32.PostMessageW(window["hwnd"], _WM_CLOSE, 0, 0)
        audit.info("М'яке закриття (WM_CLOSE, вікон: %d) «%s» — причина: %s",
                   len(windows), app.get("title"), action.reason)
        pending.append({"app": app, "pids": pids, "had_windows": had_windows})

    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if not any(_alive_pids(p["pids"]) for p in pending):
            break
        time.sleep(0.25)

    results = []
    for item in pending:
        app, title = item["app"], item["app"].get("title")
        alive = _alive_pids(item["pids"])
        if not alive:
            audit.info("Закрито штатно «%s» — причина: %s", title, action.reason)
            results.append({"title": title, "status": "closed", "reason": ""})
            continue
        visible = _main_windows(_windows_of(alive))
        if visible:
            asks_to_save = any(w["cls"] == _DIALOG_CLASS for w in visible) or app.get("document")
            reason = "відкрито документ" if asks_to_save else "програма не закрилась (вікно лишилось відкритим)"
            audit.info("НЕ закрито «%s»: %s — примусово не завершую, бо в програми є вікно", title, reason)
            results.append({"title": title, "status": "kept", "reason": reason})
            continue
        targets = [(pid, ct) for pid, ct in app["targets"] if pid in alive]
        killed, errors = terminate_processes(targets, action, timeout=3)
        if errors and not killed:
            results.append({"title": title, "status": "failed", "reason": "; ".join(errors[:2])})
        else:
            audit.info("Примусово завершено «%s» (без вікон, не закрилась за %.0f с)", title, grace_s)
            results.append({"title": title, "status": "forced", "reason": ""})
    return results


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
