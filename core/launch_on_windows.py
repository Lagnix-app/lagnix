"""Автозапуск самого Lagnix разом із Windows через завдання Планувальника
(«Виконувати з найвищими правами», тригер — вхід користувача), окремо від
core/autostart.py (той керує ЧУЖИМИ програмами автозапуску). Завдання дає
запуск з правами адміністратора без вікна UAC; запис у HKCU\\...\\Run, який
використовували раніше, при першому виклику migrate() переноситься в завдання
й видаляється. Потребує прав адміністратора — Lagnix їх завжди має."""

import getpass
import os
import subprocess
import sys
import tempfile
import winreg
from xml.sax.saxutils import escape

from core.logging_setup import get_audit_logger, get_logger

_RUN_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "Lagnix"
_OLD_VALUE_NAME = "PulseFPS"  # лише для міграції
TASK_NAME = "Lagnix"
_OLD_TASK_NAME = "PulseFPS"  # лише для міграції
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_log = get_logger("core.launch_on_windows")


def _command_and_args() -> tuple[str, str]:
    """Що запускає завдання — з --minimized, щоб програма одразу згорталась у трей."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--minimized"
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pythonw):
        exe = pythonw
    return exe, f'"{os.path.join(project_root, "main.py")}" --minimized'


def _task_xml() -> str:
    exe, args = _command_and_args()
    user = escape(f"{os.environ.get('USERDOMAIN', '')}\\{getpass.getuser()}".lstrip("\\"))
    workdir = escape(os.path.dirname(exe if getattr(sys, "frozen", False) else os.path.abspath(sys.argv[0])))
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Start Lagnix at Windows sign-in</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{user}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(exe)}</Command><Arguments>{escape(args)}</Arguments><WorkingDirectory>{workdir}</WorkingDirectory></Exec></Actions>
</Task>"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["schtasks", *args], capture_output=True, creationflags=_NO_WINDOW, timeout=30,
    )


def _run_value_exists() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_SUBKEY, 0, winreg.KEY_READ) as key:
            for name in (_VALUE_NAME, _OLD_VALUE_NAME):
                try:
                    winreg.QueryValueEx(key, name)
                    return True
                except OSError:
                    continue
    except OSError:
        pass
    return False


def _delete_run_value() -> None:
    for name in (_VALUE_NAME, _OLD_VALUE_NAME):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_SUBKEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except OSError:
            pass


def is_enabled() -> bool:
    try:
        return _schtasks("/Query", "/TN", TASK_NAME).returncode == 0
    except (OSError, subprocess.SubprocessError):
        _log.exception("Failed to check the startup task")
        return False


def set_enabled(enabled: bool) -> tuple[bool, str]:
    try:
        if enabled:
            # schtasks вимагає UTF-16 для XML із заголовком UTF-16.
            fd, path = tempfile.mkstemp(suffix=".xml")
            try:
                with os.fdopen(fd, "w", encoding="utf-16") as f:
                    f.write(_task_xml())
                proc = _schtasks("/Create", "/TN", TASK_NAME, "/XML", path, "/F")
            finally:
                os.remove(path)
            if proc.returncode != 0:
                err = proc.stderr.decode("oem", errors="replace").strip()
                _log.error("schtasks /Create: %s", err)
                return False, err
            _delete_run_value()
            get_audit_logger().info("Lagnix startup task created (runs at user logon, --minimized)")
        else:
            if is_enabled():
                proc = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
                if proc.returncode != 0:
                    err = proc.stderr.decode("oem", errors="replace").strip()
                    _log.error("schtasks /Delete: %s", err)
                    return False, err
            _delete_run_value()
            get_audit_logger().info("Lagnix startup task removed")
        return True, ""
    except (OSError, subprocess.SubprocessError) as exc:
        _log.exception("Startup task management error")
        return False, str(exc)


def _migrate_old_name() -> None:
    """Завдання «PulseFPS» → «Lagnix»: нове створюється, старе видаляється."""
    try:
        if _schtasks("/Query", "/TN", _OLD_TASK_NAME).returncode != 0:
            return
        ok, err = set_enabled(True)
        if not ok:
            _log.error("Startup task rename failed: %s", err)
            return
        _schtasks("/Delete", "/TN", _OLD_TASK_NAME, "/F")
        _log.info("Startup task renamed: %s -> %s", _OLD_TASK_NAME, TASK_NAME)
    except (OSError, subprocess.SubprocessError):
        _log.exception("Startup task rename error")


def migrate() -> None:
    """Старий автозапуск через HKCU\\Run → завдання Планувальника; завдання
    зі старою назвою → нова назва."""
    _migrate_old_name()
    if _run_value_exists():
        ok, err = set_enabled(True)
        if not ok:
            _log.error("Startup migration failed: %s", err)
