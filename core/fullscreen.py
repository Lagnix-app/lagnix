"""Чи запущена зараз гра в ексклюзивному повноекранному режимі (D3D fullscreen).

Лише читання: SHQueryUserNotificationState (shell32) — стан, який Windows показує сама. До процесів і
вікон гри Lagnix не звертається. У такому режимі оверлей (звичайне вікно поверх усіх) не видно.
"""

import ctypes
import sys

_QUNS_RUNNING_D3D_FULL_SCREEN = 3

_query = None
if sys.platform == "win32":
    try:
        _query = ctypes.WinDLL("shell32").SHQueryUserNotificationState
        _query.argtypes = (ctypes.POINTER(ctypes.c_int),)
        _query.restype = ctypes.c_long
    except (OSError, AttributeError):
        _query = None


def is_exclusive_fullscreen() -> bool:
    if _query is None:
        return False
    state = ctypes.c_int(0)
    try:
        if _query(ctypes.byref(state)) != 0:  # S_OK
            return False
    except OSError:
        return False
    return state.value == _QUNS_RUNNING_D3D_FULL_SCREEN
