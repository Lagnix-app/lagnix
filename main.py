"""Точка входу PulseFPS."""

import os
import sys

import customtkinter as ctk

from core.sounds import ensure_sounds_exist
from ui.main_window import MainWindow

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
