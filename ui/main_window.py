"""Головне вікно Lagnix з бічним меню та вкладками."""

import os

import customtkinter as ctk

from core.app_data import load_data, update_data
from core.logging_setup import get_logger
from core.settings import load_settings
from core import i18n
from core.i18n import t
from ui import bg, theme
from ui.app_shell import AppShell
from ui.widgets.dropdown import Dropdown
from ui.widgets.logo_widget import LogoWidget
from ui.widgets.support_dialog import SupportDialog
from core import links
import math
import time
from ui.monitor_tab import MonitorTab
from ui.game_mode_tab import GameModeTab
from ui.network_tab import NetworkTab
from ui.cleanup_tab import CleanupTab
from ui.programs_tab import ProgramsTab
from ui.autostart_tab import AutostartTab
from ui.registry_tweaks_tab import RegistryTweaksTab
from ui.system_tab import SystemTab
from ui.settings_tab import SettingsTab

# (ключ вкладки, ключ перекладу назви, клас)
TABS = (
    ("monitor", "tabs.monitor", MonitorTab),
    ("game_mode", "tabs.game_mode", GameModeTab),
    ("network", "tabs.network", NetworkTab),
    ("cleanup", "tabs.cleanup", CleanupTab),
    ("programs", "tabs.programs", ProgramsTab),
    ("autostart", "tabs.autostart", AutostartTab),
    ("registry_tweaks", "tabs.registry_tweaks", RegistryTweaksTab),
    ("system", "tabs.system", SystemTab),
    ("settings", "tabs.settings", SettingsTab),
)

_ICON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "lagnix.ico"
)

_INDICATOR_WIDTH = 3
_PULSE_LOW = theme.BORDER  # рамка кнопки «Підтримати»: від спокійної до рожевої
_PULSE_HIGH = "#ff5e5b"
_TAB_SLIDE_OFFSET = 16


class MainWindow(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.settings = load_settings()
        theme.set_animations_enabled(self.settings.get("animations_enabled", True))
        theme.set_robot_animation_enabled(self.settings.get("robot_animation_enabled", True))

        self.title("Lagnix")
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
        self._stale_tabs: set[str] = set()  # побудовані попередньою мовою — перебудувати при показі
        self._language_job = None
        theme.apply_language_fonts(self)

        self.protocol("WM_DELETE_WINDOW", self._on_window_close)
        bg.ensure_pump(self)  # доставка результатів фонових потоків (ui/bg.py)

        self._build_sidebar()
        self._build_content_area()

        # трей, гарячі клавіші, оверлей і сповіщення Windows (ui/app_shell.py)
        self.shell = AppShell(self)
        self.shell.start()

        i18n.on_change(self._on_language_changed)

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
        try:
            self.shell.shutdown()
        except Exception:
            get_logger(__name__).exception("Shell shutdown failed")  # вікно все одно має закритись
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
                text=t(label),
                anchor="w",
                corner_radius=8,
                fg_color="transparent",
                hover_color=theme.BG_PANEL_LIGHT,
                text_color=theme.TEXT_MAIN,
                command=lambda k=key: self._select_tab(k)
            )
            button.grid(row=index, column=0, padx=10, pady=4, sticky="ew")
            self.nav_buttons[key] = button

        self._support_button = None
        self._support_dialog = None
        self._pulse_job = None
        if links.KOFI_URL:
            self._support_button = ctk.CTkButton(
                sidebar, text="❤ " + t("support.button"), height=34, corner_radius=8, fg_color="transparent",
                border_width=1, border_color=_PULSE_LOW, hover_color=theme.BG_PANEL_LIGHT,
                text_color=theme.TEXT_MAIN, command=self._open_support)
            self._support_button.grid(row=spacer_row + 1, column=0, padx=10, pady=(4, 14), sticky="ew")
            self._pulse()

    def _open_support(self) -> None:
        dialog = self._support_dialog
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            dialog.focus_force()
            return
        self._support_dialog = SupportDialog(self)

    def _pulse(self) -> None:
        """Лёгка пульсація рамки кнопки «Підтримати» (~12 к/с); на паузі, поки вікно згорнуте."""
        self._pulse_job = None
        button = self._support_button
        if button is None:
            return
        try:
            shown = self.state() not in ("iconic", "withdrawn")
            if shown and theme.animations_enabled():
                k = (math.sin(time.perf_counter() * 2 * math.pi / 2.4) + 1) / 2
                button.configure(border_color=theme.lerp_color(self, _PULSE_LOW, _PULSE_HIGH, k))
            self._pulse_job = self.after(90, self._pulse)
        except Exception:
            pass

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

    # ---------------------------------------------------------- зміна мови

    def _on_language_changed(self, _code: str) -> None:
        """i18n.set_language() кличуть із колбека віджета на вкладці «Налаштування» —
        перебудова її ж посеред власного обробника зламала б CTk, тож — наступним тактом."""
        if self._language_job is None:
            self._language_job = self.after(10, self._apply_language)

    def _apply_language(self) -> None:
        """Нова мова — без перезапуску: меню, трей, оверлей одразу; поточна вкладка
        перебудовується зараз (з тією ж прокруткою), решта — при першому показі.
        «Ігровий режим» перебудовує лише вигляд, зберігаючи стан і фонові потоки."""
        self._language_job = None
        theme.apply_language_fonts(self)
        for key, label, _frame_cls in TABS:
            self.nav_buttons[key].configure(text=t(label), font=ctk.CTkFont())
        if self._support_button is not None:
            self._support_button.configure(text="❤ " + t("support.button"), font=ctk.CTkFont())
        self._stale_tabs = {key for key in self.tab_frames if key != "game_mode"}
        game_tab = self.tab_frames.get("game_mode")
        if game_tab is not None:
            game_tab.rebuild_view()
        if self._current_tab is not None:
            self._refresh_stale_tab(self._current_tab)
        self.shell.on_language_changed()

    def _refresh_stale_tab(self, key: str) -> bool:
        """Перебудувати вкладку новою мовою, якщо вона застаріла й не зайнята.
        Зайнята (очищення, тест мережі, видалення…) — повторна спроба, доки її видно."""
        if key not in self._stale_tabs:
            return True
        busy = getattr(self.tab_frames[key], "is_busy", None)
        if busy is not None and busy():
            self.after(1000, lambda: self._refresh_stale_tab(key) if key == self._current_tab else None)
            return False
        self._stale_tabs.discard(key)
        self._rebuild_tab(key)
        return True

    def _rebuild_tab(self, key: str) -> None:
        old = self.tab_frames[key]
        frame_cls = next(cls for k, _label, cls in TABS if k == key)
        fraction = _scroll_fraction(old)
        shown = key == self._current_tab and bool(old.winfo_manager())
        self._tab_anim[key].cancel()
        old.place_forget()
        old.destroy()  # <Destroy> вкладки зупиняє її фонові потоки
        frame = frame_cls(self.content_area)
        self.tab_frames[key] = frame
        self._tab_anim[key] = theme.ValueAnimator(frame, lambda v, f=frame: f.place_configure(y=round(v)))
        self.shell.on_tab_rebuilt(key, frame)
        if shown:
            frame.place(relx=0, rely=0, y=0, relwidth=1, relheight=1)
            frame.lift()
            self._update_visibility()
            if fraction:
                self.after(150, lambda: _restore_scroll(frame, fraction))

    def select_tab(self, key: str) -> None:
        """Публічна навігація для кнопок з інших вкладок (напр. підказки, звіт)."""
        self._select_tab(key)

    def _select_tab(self, key: str):
        Dropdown.close_all()
        if key == self._current_tab:
            return
        previous, self._current_tab = self._current_tab, key
        if key in self._stale_tabs:
            self._refresh_stale_tab(key)  # ще не розміщена — перебудова без зайвого показу

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


def _scroll_container(frame):
    """ScrollFrame (self.scroll) або ScrollPage (self.page) вкладки — у обох є canvas і controller."""
    for name in ("scroll", "page"):
        container = getattr(frame, name, None)
        if container is not None and hasattr(container, "canvas") and hasattr(container, "controller"):
            return container
    return None


def _scroll_fraction(frame) -> float:
    container = _scroll_container(frame)
    try:
        return float(container.canvas.yview()[0]) if container is not None else 0.0
    except Exception:
        return 0.0


def _restore_scroll(frame, fraction: float) -> None:
    container = _scroll_container(frame)
    try:
        if container is not None and frame.winfo_exists():
            container.controller.moveto_now(fraction)
    except Exception:
        pass
