"""Головне вікно PulseFPS з бічним меню та вкладками."""

import customtkinter as ctk

from core.settings import load_settings, update_setting
from ui.monitor_tab import MonitorTab
from ui.game_mode_tab import GameModeTab
from ui.network_tab import NetworkTab
from ui.cleanup_tab import CleanupTab
from ui.programs_tab import ProgramsTab
from ui.autostart_tab import AutostartTab
from ui.registry_tweaks_tab import RegistryTweaksTab
from ui.system_tab import SystemTab
from ui.settings_tab import SettingsTab

TABS = (
    ("monitor", "Монітор", MonitorTab),
    ("game_mode", "Ігровий режим", GameModeTab),
    ("network", "Мережа", NetworkTab),
    ("cleanup", "Очищення", CleanupTab),
    ("programs", "Програми", ProgramsTab),
    ("autostart", "Автозапуск", AutostartTab),
    ("registry_tweaks", "Твіки реєстру", RegistryTweaksTab),
    ("system", "Система", SystemTab),
    ("settings", "Налаштування", SettingsTab),
)


class MainWindow(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.settings = load_settings()

        self.title("PulseFPS")
        width = self.settings.get("window", {}).get("width", 1100)
        height = self.settings.get("window", {}).get("height", 700)
        self.geometry(f"{width}x{height}")
        self.minsize(900, 600)

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.nav_buttons = {}
        self.tab_frames = {}

        self._build_sidebar()
        self._build_content_area()

        start_tab = self.settings.get("last_tab", TABS[0][0])
        if start_tab not in self.tab_frames:
            start_tab = TABS[0][0]
        self._select_tab(start_tab)

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nswe")
        sidebar.grid_rowconfigure(len(TABS) + 1, weight=1)

        logo_label = ctk.CTkLabel(
            sidebar,
            text="PulseFPS",
            font=ctk.CTkFont(size=20, weight="bold")
        )
        logo_label.grid(row=0, column=0, padx=20, pady=(20, 15), sticky="w")

        for index, (key, label, _frame_cls) in enumerate(TABS, start=1):
            button = ctk.CTkButton(
                sidebar,
                text=label,
                anchor="w",
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray75", "gray25"),
                command=lambda k=key: self._select_tab(k)
            )
            button.grid(row=index, column=0, padx=10, pady=4, sticky="ew")
            self.nav_buttons[key] = button

    def _build_content_area(self):
        self.content_area = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content_area.grid(row=0, column=1, sticky="nswe")
        self.content_area.grid_rowconfigure(0, weight=1)
        self.content_area.grid_columnconfigure(0, weight=1)

        for key, _label, frame_cls in TABS:
            frame = frame_cls(self.content_area)
            frame.grid(row=0, column=0, sticky="nswe")
            self.tab_frames[key] = frame

    def _select_tab(self, key: str):
        for tab_key, frame in self.tab_frames.items():
            if tab_key == key:
                frame.tkraise()

        for tab_key, button in self.nav_buttons.items():
            if tab_key == key:
                button.configure(fg_color=("gray75", "gray25"))
            else:
                button.configure(fg_color="transparent")

        self.settings["last_tab"] = key
        update_setting("last_tab", key)
