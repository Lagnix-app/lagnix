"""Перевірка та підвищення прав адміністратора PulseFPS."""

import ctypes
import os
import sys


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch_as_admin() -> bool:
    """Просить Windows перезапустити поточний процес з правами адміністратора.

    Викликає ShellExecuteW з дієсловом "runas" — Windows сама показує
    стандартне вікно UAC. Повертає True, якщо запит вдалося передати системі
    (вікно UAC з'явиться); викликач після цього має завершити поточний
    екземпляр. False повертається лише при системній помилці запуску
    (напр. файл не знайдено) — відмову користувача у вікні UAC
    ShellExecuteW не повідомляє.
    """
    try:
        exe = sys.executable
        if getattr(sys, "frozen", False):
            args = sys.argv[1:]
        else:
            args = [os.path.abspath(sys.argv[0]), *sys.argv[1:]]
        params = " ".join(f'"{arg}"' for arg in args)

        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return result > 32
    except (AttributeError, OSError):
        return False
