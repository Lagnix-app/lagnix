"""Точка входу PulseFPS."""

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

import customtkinter as ctk  # noqa: E402

from core.sounds import ensure_sounds_exist  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

_THEME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "pulsefps_theme.json")


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme(_THEME_PATH if os.path.exists(_THEME_PATH) else "blue")

    ensure_sounds_exist()

    app = MainWindow()
    if "--minimized" in sys.argv[1:]:
        app.start_minimized()
    app.mainloop()


if __name__ == "__main__":
    main()
