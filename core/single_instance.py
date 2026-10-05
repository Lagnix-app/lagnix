"""Один екземпляр Lagnix: другий запуск не відкриває ще одне вікно (дві іконки в треї,
гонка за гарячі клавіші й data.json), а просить перший показати своє вікно.

Іменований mutex — «хто перший», іменована подія — «покажи вікно». Слухач — окремий
потік, який Tk не чіпає: колбек лише кладе виклик у чергу інтерфейсу (ui/bg.ui_call)."""

import ctypes
import sys
import threading

from core.logging_setup import get_logger

_MUTEX_NAME = r"Local\Lagnix.SingleInstance"
_EVENT_NAME = r"Local\Lagnix.ShowWindow"
_ERROR_ALREADY_EXISTS = 183
_ERROR_ACCESS_DENIED = 5
_EVENT_MODIFY_STATE = 0x0002
_WAIT_OBJECT_0 = 0
_log = get_logger("core.single_instance")

_mutex = None
_listener_stop = threading.Event()


def _kernel32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateEventW.restype = ctypes.c_void_p
    k32.OpenEventW.restype = ctypes.c_void_p
    k32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, ctypes.c_wchar_p]
    k32.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p]
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k32.SetEvent.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return k32


def acquire() -> bool:
    """True — ми перший (або перевірка неможлива); False — Lagnix уже запущено."""
    global _mutex
    if sys.platform != "win32":
        return True
    try:
        k32 = _kernel32()
        handle = k32.CreateMutexW(None, False, _MUTEX_NAME)
        error = ctypes.get_last_error()
        if not handle:
            return error != _ERROR_ACCESS_DENIED  # mutex чужий (інший рівень прав) — отже, вже запущено
        if error == _ERROR_ALREADY_EXISTS:
            k32.CloseHandle(handle)
            return False
        _mutex = handle  # тримаємо до кінця процесу
        return True
    except (AttributeError, OSError):
        _log.exception("Single-instance check failed; continuing")
        return True


def signal_existing() -> None:
    """Просить уже запущений Lagnix показати вікно."""
    try:
        k32 = _kernel32()
        event = k32.OpenEventW(_EVENT_MODIFY_STATE, False, _EVENT_NAME)
        if event:
            k32.SetEvent(event)
            k32.CloseHandle(event)
    except (AttributeError, OSError):
        _log.exception("Could not signal the running instance")


def listen(callback) -> None:
    """Фоновий потік: callback() щоразу, коли другий запуск просить показати вікно."""
    if sys.platform != "win32":
        return
    try:
        k32 = _kernel32()
        event = k32.CreateEventW(None, False, False, _EVENT_NAME)
    except (AttributeError, OSError):
        _log.exception("Could not create the show-window event")
        return
    if not event:
        return

    def run():
        try:
            while not _listener_stop.is_set():
                if k32.WaitForSingleObject(event, 500) == _WAIT_OBJECT_0:
                    callback()
        except Exception:
            _log.exception("Single-instance listener failed")
        finally:
            k32.CloseHandle(event)

    threading.Thread(target=run, name="single-instance", daemon=True).start()


def stop() -> None:
    _listener_stop.set()
