"""Автозапуск самого PulseFPS разом із Windows (HKCU\\...\\Run), окремо від
core/autostart.py (той керує ЧУЖИМИ програмами автозапуску). Стан читається
наживо з реєстру (як і твіки/автозапуск в інших вкладках) — HKCU не
потребує прав адміністратора."""

import os
import sys
import winreg

_RUN_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "PulseFPS"


def _launch_command() -> str:
    """Команда, яку Windows виконає при вході — з прапорцем --minimized,
    щоб програма одразу згорталась у трей, а не блимала вікном."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(project_root, "main.py")
    return f'"{sys.executable}" "{script}" --minimized'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_SUBKEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> tuple[bool, str]:
    try:
        if enabled:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_SUBKEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_SUBKEY, 0, winreg.KEY_SET_VALUE) as key:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True, ""
    except OSError as exc:
        return False, str(exc)
