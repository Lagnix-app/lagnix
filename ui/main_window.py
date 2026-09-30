"""Головне вікно PulseFPS з бічним меню та вкладками."""

import os

import customtkinter as ctk

from core.app_data import load_data, update_data
from core.settings import load_settings
from ui import bg, theme
from ui.app_shell import AppShell
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
        theme.set_animations_enabled(self.settings.get("animations_enabled", True))
        theme.set_robot_animation_enabled(self.settings.get("robot_animation_enabled", True))

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

        self.protocol("WM_DELETE_WINDOW", self._on_window_close)
        bg.ensure_pump(self)  # доставка результатів фонових потоків (ui/bg.py)

        self._build_sidebar()
        self._build_content_area()

        # трей, гарячі клавіші, оверлей і сповіщення Windows (ui/app_shell.py)
        self.shell = AppShell(self)
        self.shell.start()

        # згорнуте/сховане в трей вікно — фонові оновлення й анімації на паузі
        self.bind("<Map>", self._on_root_map_change, add="+")
        self.bind("<Unmap>", self._on_root_map_change, add="+")

        if self.settings.get("startup_tab_mode", "last") == "monitor":
            start_tab = TABS[0][0]
        else:
            start_tab = load_data().get("last_tab", TABS[0][0])
        if start_tab not in self.tab_frames:
            start_tab = TABS[0][0]
        self._select_tab(start_tab)

    # -------------------------------------------------------- трей/закриття

    def start_minimized(self) -> None:
        """Викликається з main.py при запуску з --minimized (автозапуск
        Windows) — вікно одразу ховається в трей, без блимання на екрані."""
        if not self.shell.tray.visible:
            return  # трею немає — лишаємо вікно видимим, щоб програма не "зникла"
        self.withdraw()

    def _on_window_close(self) -> None:
        close_action = load_settings().get("close_action", "exit")
        if close_action == "tray" and self.shell.tray.visible:
            self.shell.hide_window()
        else:
            self.exit_app()

    def show_window(self, tab: str | None = None) -> None:
        """Показати вікно (з трею/згорнутого), за потреби — на вкладці tab."""
        self.shell.show_window(tab)

    # вкладки викликають це й у своїх конструкторах — тобто ще до створення self.shell
    def on_game_mode_changed(self, active: bool) -> None:
        shell = getattr(self, "shell", None)
        if shell is not None:
            shell.on_game_mode_changed(active)

    def notify_game_mode_auto(self, game_name: str) -> None:
        shell = getattr(self, "shell", None)
        if shell is not None:
            shell.notify_game_mode_auto(game_name)

    def exit_app(self) -> None:
        self.shell.shutdown()
        self.destroy()

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

        logo = self._logo = LogoWidget(sidebar)
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

    def _place_indicator(self, y: float) -> None:
        self._indicator.place(x=4, y=round(y))

    def _build_content_area(self):
        self.content_area = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content_area.grid(row=0, column=1, sticky="nswe")
        self.content_area.grid_rowconfigure(0, weight=1)
        self.content_area.grid_columnconfigure(0, weight=1)

        # Вкладки не розміщуються, доки їх не покажуть: прихована вкладка знята
        # з розкладки (place_forget), а не просто перекрита lift() — інакше Tk
        # вважає її видимою (winfo_ismapped), Windows обрізає/перемальовує всі
        # 9 накладених шарів, а перевірки "чи видно" в Моніторі не спрацьовують.
        for key, _label, frame_cls in TABS:
            frame = frame_cls(self.content_area)
            self.tab_frames[key] = frame
            self._tab_anim[key] = theme.ValueAnimator(
                frame, lambda v, f=frame: f.place_configure(y=round(v))
            )

    def _on_root_map_change(self, event) -> None:
        if event.widget is self:
            self._update_visibility()

    def _update_visibility(self) -> None:
        """Сповіщає вкладки (on_visibility_changed(bool), якщо є) і лого про
        те, чи їх зараз видно: поточна вкладка у не згорнутому вікні."""
        try:
            shown = self.state() not in ("iconic", "withdrawn")
        except Exception:
            shown = True
        self._logo.set_paused(not shown)
        for key, frame in self.tab_frames.items():
            visible = shown and key == self._current_tab
            if getattr(frame, "_tab_visible", None) == visible:
                continue
            frame._tab_visible = visible
            hook = getattr(frame, "on_visibility_changed", None)
            if hook is not None:
                hook(visible)

    def select_tab(self, key: str) -> None:
        """Публічна навігація для кнопок з інших вкладок (напр. підказки, звіт)."""
        self._select_tab(key)

    def _select_tab(self, key: str):
        if key == self._current_tab:
            return
        previous, self._current_tab = self._current_tab, key

        frame = self.tab_frames[key]
        frame.place(relx=0, rely=0, y=_TAB_SLIDE_OFFSET, relwidth=1, relheight=1)
        frame.lift()
        if previous is not None:
            self._tab_anim[previous].cancel()
            self.tab_frames[previous].place_forget()
        self._update_visibility()
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

        update_data("last_tab", key)
