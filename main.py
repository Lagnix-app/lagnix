"""Точка входу Lagnix."""

import ctypes
import os
import sys


def _enable_dpi_awareness() -> None:
    """Per-monitor DPI awareness до створення будь-якого вікна Tk: без неї
    Windows розтягує вікно (розмито) на екранах із масштабом 125%/150%.
    Малювання (ui/widgets/aa.py) враховує масштаб екрана, тож графіка чітка."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


_enable_dpi_awareness()

from core import admin as admin_core  # noqa: E402
from core import i18n  # noqa: E402
from core.settings import load_settings  # noqa: E402


def _ensure_admin() -> None:
    """Lagnix завжди працює з правами адміністратора: без них — перезапуск
    через UAC ("runas") і вихід із поточного процесу. Якщо користувач відмовив
    у вікні UAC — пропонуємо спробувати ще раз або вийти."""
    if admin_core.is_admin():
        return
    while True:
        if admin_core.relaunch_as_admin():
            sys.exit(0)
        if not admin_core.ask_retry_admin():
            sys.exit(0)

import customtkinter as ctk  # noqa: E402

from core import launch_on_windows, rename_migrate, restart, sensors, single_instance  # noqa: E402
from core.sounds import ensure_sounds_exist  # noqa: E402
from ui import bg  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

_THEME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "lagnix_theme.json")


def main():
    i18n.set_language(load_settings().get("language"))  # до першого вікна (зокрема запиту прав)
    _ensure_admin()
    restart.wait_for_previous()  # перезапуск (зміна мови): старий екземпляр має встигнути вийти
    if not single_instance.acquire():  # Lagnix уже запущено — показуємо його вікно й виходимо
        single_instance.signal_existing()
        return

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme(_THEME_PATH if os.path.exists(_THEME_PATH) else "blue")

    ensure_sounds_exist()
    launch_on_windows.migrate()
    rename_migrate.run()  # PulseFPS -> Lagnix: теки автозапуску, назва плану Ultra
    sensors.set_enabled(load_settings().get("advanced_sensors_enabled", True))

    app = MainWindow()
    restart_state = restart.pop_state()
    if restart_state:
        app.apply_restart_state(restart_state)
    elif "--minimized" in sys.argv[1:]:
        app.start_minimized()
    single_instance.listen(lambda: bg.ui_call(app, app.shell.show_window))
    try:
        app.mainloop()
    finally:
        single_instance.stop()
        app.shell.shutdown()  # трей і потік гарячих клавіш (повторний виклик — без ефекту)
        sensors.stop()


if __name__ == "__main__":
    main()
