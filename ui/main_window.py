"""Головне вікно PulseFPS з бічним меню та вкладками."""

import os

import customtkinter as ctk

from core.settings import load_settings, update_setting
from ui import theme
from ui.widgets.logo_widget import LogoWidget
from ui.monitor_tab import MonitorTab
from ui.game_mode_tab import GameModeTab
from ui.network_tab import NetworkTab
from ui.cleanup_tab import CleanupTab
from ui.programs_tab import ProgramsTab
from ui.autostart_tab import AutostartTab
from ui.registry_tweaks_tab import RegistryTweaksTab
from ui.system_tab import SystemTab
from ui.settings_tab import SettingsTab
from ui.admin_status import AdminStatusPanel

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

_ICON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "pulsefps.ico"
)

_INDICATOR_WIDTH = 3
_TAB_SLIDE_OFFSET = 16


class MainWindow(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.settings = load_settings()

        self.title("PulseFPS")
        self._apply_icon()
        width = self.settings.get("window", {}).get("width", 1100)
        height = self.settings.get("window", {}).get("height", 700)
        self.geometry(f"{width}x{height}")
        self.minsize(900, 600)

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.nav_buttons = {}
        self.tab_frames = {}
        self._tab_anim = {}
        self._current_tab = None

        self._build_sidebar()
        self._build_content_area()

        start_tab = self.settings.get("last_tab", TABS[0][0])
        if start_tab not in self.tab_frames:
            start_tab = TABS[0][0]
        self._select_tab(start_tab)

    def _apply_icon(self) -> None:
        if os.path.exists(_ICON_PATH):
            try:
                self.iconbitmap(_ICON_PATH)
            except Exception:
                pass

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=theme.BG_PANEL)
        sidebar.grid(row=0, column=0, sticky="nswe")
        spacer_row = len(TABS) + 1
        sidebar.grid_rowconfigure(spacer_row, weight=1)

        logo = LogoWidget(sidebar)
        logo.grid(row=0, column=0, padx=(6, 6), pady=(12, 12), sticky="ew")

        self._indicator = theme.PlainFrame(
            sidebar, width=_INDICATOR_WIDTH, height=10, corner_radius=2, fg_color=theme.ACCENT_GREEN,
        )
        self._indicator_height = 10
        self._indicator_anim = theme.ValueAnimator(sidebar, self._place_indicator)

        for index, (key, label, _frame_cls) in enumerate(TABS, start=1):
            button = theme.NavButton(
                sidebar,
                text=label,
                anchor="w",
                corner_radius=8,
                fg_color="transparent",
                hover_color=theme.BG_PANEL_LIGHT,
                text_color=theme.TEXT_MAIN,
                command=lambda k=key: self._select_tab(k)
            )
            button.grid(row=index, column=0, padx=10, pady=4, sticky="ew")
            self.nav_buttons[key] = button

        admin_panel = AdminStatusPanel(sidebar)
        admin_panel.grid(row=spacer_row + 1, column=0, padx=14, pady=(6, 16), sticky="ew")

    def _place_indicator(self, y: float) -> None:
        self._indicator.place(x=4, y=round(y))

    def _build_content_area(self):
        self.content_area = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content_area.grid(row=0, column=1, sticky="nswe")
        self.content_area.grid_rowconfigure(0, weight=1)
        self.content_area.grid_columnconfigure(0, weight=1)

        for key, _label, frame_cls in TABS:
            frame = frame_cls(self.content_area)
            frame.place(relx=0, rely=0, y=0, relwidth=1, relheight=1)
            self.tab_frames[key] = frame
            self._tab_anim[key] = theme.ValueAnimator(
                frame, lambda v, f=frame: f.place_configure(y=round(v))
            )

    def _select_tab(self, key: str):
        if key == self._current_tab:
            return
        self._current_tab = key

        frame = self.tab_frames[key]
        frame.lift()
        anim = self._tab_anim[key]
        anim.set_immediate(_TAB_SLIDE_OFFSET)
        anim.animate_to(0, duration=0.2)

        for tab_key, button in self.nav_buttons.items():
            if tab_key == key:
                button.configure(fg_color=theme.BG_PANEL_LIGHT)
            else:
                button.configure(fg_color="transparent")

        button = self.nav_buttons[key]
        self.update_idletasks()
        self._indicator_height = max(button.winfo_height() - 10, 10)
        self._indicator.configure(height=self._indicator_height)
        target_y = button.winfo_y() + (button.winfo_height() - self._indicator_height) / 2
        self._indicator_anim.animate_to(target_y, duration=0.22)

        self.settings["last_tab"] = key
        update_setting("last_tab", key)
