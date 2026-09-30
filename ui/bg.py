"""Фонові завдання й передача їхніх результатів в інтерфейс — без звернень до Tk з інших потоків.

Чому не widget.after(0, ...) з фонового потоку: tkinter дозволяє це лише коли
головний потік уже крутиться в mainloop. customtkinter перед стартом
mainloop викликає update() (застосування темного заголовка вікна) — у ньому
спрацьовують усі after(0) вкладок, фонові потоки стартують раніше за mainloop, і
їхній after() через секунду падає з RuntimeError: main thread is not in main
loop. Раніше цю помилку мовчки ковтали — і вкладка назавжди лишалась
«Завантаження…».

Тут:
  * ui_call(owner, fn, *args) — потокобезпечно: кладе виклик у чергу (Tk не
    чіпає, ніколи не блокує і не падає). Головний потік розбирає чергу таймером
    (pump), тож результат дійде, щойно запрацює mainloop;
  * run_task(...) — окремий потік на кожне завдання (завдання різних вкладок не
    чекають одне одного), тайм-аут, будь-який виняток — у logs.txt і в on_error.
"""

import queue
import threading
import time
import traceback

from core.logging_setup import get_logger
from ui import theme

DEFAULT_TIMEOUT_S = 60.0
_PUMP_INTERVAL_MS = 25
_MAX_HOLD_S = 1.0        # скільки найдовше тримати результати під час безперервної прокрутки
_PUMP_BUDGET_S = 0.03  # не більше 30 мс роботи за такт — інтерфейс не підвисає

_log = get_logger("ui.bg")
_queue: "queue.SimpleQueue[tuple]" = queue.SimpleQueue()
_pumped_roots: set[str] = set()


def _alive(widget) -> bool:
    try:
        return bool(widget.winfo_exists())
    except Exception:
        return False


def _install_exception_hooks(root) -> None:
    """Непіймані винятки будь-якого потоку й колбеків Tk — у logs.txt, а не лише в консоль."""
    if getattr(threading.excepthook, "_pulsefps", False) is False:
        previous = threading.excepthook

        def thread_hook(args):
            if args.exc_type is not SystemExit:
                _log.error("Непійманий виняток у потоці %s:\n%s", getattr(args.thread, "name", "?"),
                           "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
            previous(args)

        thread_hook._pulsefps = True
        threading.excepthook = thread_hook

    def callback_hook(exc, value, tb):
        _log.error("Непійманий виняток в обробнику інтерфейсу:\n%s", "".join(traceback.format_exception(exc, value, tb)))
        traceback.print_exception(exc, value, tb)

    root.report_callback_exception = callback_hook


def ensure_pump(widget) -> None:
    """Запускає розбір черги для вікна widget (ідемпотентно). Лише з головного потоку."""
    root = widget.winfo_toplevel()
    key = str(root)
    if key in _pumped_roots:
        return
    _pumped_roots.add(key)
    _install_exception_hooks(root)

    held_since = [0.0]

    def pump():
        # під час прокрутки результати живих оновлень чекають у черзі (не довше
        # _MAX_HOLD_S) і застосовуються вже після її зупинки
        now = time.perf_counter()
        if theme.is_scrolling() and not _queue.empty():
            held_since[0] = held_since[0] or now
            hold = now - held_since[0] < _MAX_HOLD_S
        else:
            held_since[0], hold = 0.0, False
        deadline = now + _PUMP_BUDGET_S
        while not hold and time.perf_counter() < deadline:
            try:
                owner, fn, args = _queue.get_nowait()
            except queue.Empty:
                break
            if owner is not None and not _alive(owner):
                continue
            try:
                fn(*args)
            except Exception:
                _log.exception("Помилка в обробнику результату фонового завдання (%s)",
                               getattr(fn, "__qualname__", fn))
        if _alive(root):
            root.after(_PUMP_INTERVAL_MS, pump)
        else:
            _pumped_roots.discard(key)

    root.after(_PUMP_INTERVAL_MS, pump)


def ui_call(owner, fn, *args) -> None:
    """Виконати fn(*args) у потоці інтерфейсу (якщо owner ще існує). Безпечно з будь-якого потоку."""
    _queue.put((owner, fn, args))


class WidgetBool:
    """Замінник ctk.BooleanVar для віджетів, що створюються й знищуються динамічно.

    tkinter.Variable.__del__ звертається до Tk; якщо збирач сміття звільнить
    змінну знищеного віджета, поки Python працює у фоновому потоці, це виклик
    Tk не з того потоку (може закінчитись Tcl_AsyncDelete і аварією). Тут стан
    живе в самому CTkSwitch/CTkCheckBox (select/deselect/get) — Tk-змінної немає."""

    def __init__(self, widget):
        self._widget = widget

    def get(self) -> bool:
        return bool(self._widget.get())

    def set(self, value: bool) -> None:
        if value:
            self._widget.select()
        else:
            self._widget.deselect()


class Task:
    """Хендл запущеного завдання: done / timed_out; cancel() — результат більше не цікавий."""

    def __init__(self, name: str):
        self.name = name
        self.finished = False
        self.timed_out = False

    def cancel(self) -> None:
        self.finished = True


def error_text(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return str(exc) or "перевищено час очікування"
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def run_task(owner, name: str, fn, on_done, on_error, timeout: float = DEFAULT_TIMEOUT_S) -> Task:
    """fn() — в окремому потоці. Рівно один із колбеків (у потоці UI):
    on_done(результат) або on_error(виняток) — зокрема TimeoutError, якщо fn не
    завершилась за timeout с (пізній результат тоді ігнорується).
    Викликати з головного потоку."""
    ensure_pump(owner)
    task = Task(name)
    started = time.perf_counter()

    def finish(callback, value):
        if task.finished:
            return
        task.finished = True
        callback(value)

    def worker():
        try:
            result = fn()
        except BaseException as exc:  # noqa: BLE001 — будь-що має дійти до інтерфейсу
            _log.error("Фонове завдання «%s» завершилось помилкою:\n%s", name, traceback.format_exc())
            ui_call(owner, finish, on_error, exc)
            return
        if task.timed_out:
            _log.error("Фонове завдання «%s» завершилось через %.1f с — уже після тайм-ауту, результат відкинуто",
                       name, time.perf_counter() - started)
            return
        ui_call(owner, finish, on_done, result)

    def on_timeout():
        if task.finished or not _alive(owner):
            return
        task.timed_out = True
        _log.error("Фонове завдання «%s» не завершилось за %.0f с (тайм-аут)", name, timeout)
        finish(on_error, TimeoutError(f"не завершилось за {timeout:.0f} с"))

    threading.Thread(target=worker, daemon=True, name=f"bg:{name}").start()
    owner.after(int(timeout * 1000), on_timeout)
    return task


def start_thread(owner, name: str, target, *args) -> threading.Thread:
    """Довготривалий фоновий цикл (стеження, монітор): непіймані винятки — у logs.txt,
    а не мовчки в stderr."""
    ensure_pump(owner)

    def run():
        try:
            target(*args)
        except BaseException:  # noqa: BLE001
            _log.error("Фоновий потік «%s» аварійно завершився:\n%s", name, traceback.format_exc())

    thread = threading.Thread(target=run, daemon=True, name=f"bg:{name}")
    thread.start()
    return thread
