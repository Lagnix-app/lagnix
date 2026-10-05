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
from ui.widgets.language_splash import LanguageSplash
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
_PULSE_LEVELS = 8   # кроків між спокійною й рожевою рамкою
_PULSE_MS = 120
_WARMUP_FIRST_MS = 2500   # прогрів вкладок: перша пауза після показу вікна
_WARMUP_STEP_MS = 700     # і між вкладками


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
        self._current_tab = None
        self._placed: set[str] = set()  # вкладки, розміщені в content_area (поточна + запарковані під нею)
        self._last_size = None
        self._pulse_level = -1
        self._warm_job = None
        self._stale_tabs: set[str] = set()  # побудовані попередньою мовою — перебудувати при показі
        self._language_job = None
        self._language_splash = None
        self._language_queue: list[str] = []
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
        # Окремий bindtag лише на кореневому вікні: self.bind("<Map>") спрацьовував би для КОЖНОГО
        # дочірнього віджета (~1000 викликів Python на перемикання вкладки, ~70 мс).
        self.bindtags(("LagnixRootMap",) + self.bindtags())
        self.bind_class("LagnixRootMap", "<Map>", self._on_root_map_change, add="+")
        self.bind_class("LagnixRootMap", "<Unmap>", self._on_root_map_change, add="+")
        self.bind_class("LagnixRootMap", "<Configure>", self._on_root_configure, add="+")

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
                level = round(k * _PULSE_LEVELS)  # кожне configure повністю перемальовує кнопку — міняємо лише на новому рівні
                if level != self._pulse_level:
                    self._pulse_level = level
                    button.configure(border_color=theme.lerp_color(self, _PULSE_LOW, _PULSE_HIGH, level / _PULSE_LEVELS))
            self._pulse_job = self.after(_PULSE_MS, self._pulse)
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
            self.tab_frames[key] = frame_cls(self.content_area)

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
            if visible:
                theme.fire_show(frame)  # запарковану вкладку показано без <Map> — перемалювати графіки
        if shown:
            self._schedule_warmup()

    # ---------------------------------------------------------- зміна мови

    def _on_language_changed(self, _code: str) -> None:
        """i18n.set_language() кличуть із колбека віджета на вкладці «Налаштування» —
        перебудова її ж посеред власного обробника зламала б CTk, тож — наступним тактом."""
        if self._language_job is None:
            self._language_job = self.after(10, self._apply_language)

    def _apply_language(self) -> None:
        """Нова мова — без перезапуску: заставка на все вікно, під нею оновлюються меню, трей,
        оверлей і по черзі (по одній вкладці за такт — заставка не замирає) перебудовуються
        вкладки; потім заставка плавно зникає. Прокрутка й поточна вкладка зберігаються.
        «Ігровий режим» перебудовує лише вигляд, зберігаючи стан і фонові потоки."""
        self._language_job = None
        if self._language_splash is None:
            try:
                self._language_splash = LanguageSplash(self)
            except Exception:
                get_logger(__name__).exception("Could not show the language splash")
        elif self._language_splash.label.winfo_exists():
            self._language_splash.label.configure(text=t("language.changing"))
        theme.apply_language_fonts(self)
        for key, label, _frame_cls in TABS:
            self.nav_buttons[key].configure(text=t(label), font=ctk.CTkFont())
        if self._support_button is not None:
            self._support_button.configure(text="❤ " + t("support.button"), font=ctk.CTkFont())
        self._stale_tabs = {key for key in self.tab_frames if key != "game_mode"}
        game_tab = self.tab_frames.get("game_mode")
        if game_tab is not None:
            game_tab.rebuild_view()
        # під заставкою — лише поточна вкладка; решта перебудовуються у фоні (прогрів) або при показі
        self._language_queue = [self._current_tab] if self._current_tab in self._stale_tabs else []
        self.shell.on_language_changed()
        self.after(30, self._language_step)

    def _language_step(self) -> None:
        """Одна вкладка за такт; зайняті (очищення, тест мережі…) лишаються застарілими й
        перебудуються при першому показі."""
        while self._language_queue:
            key = self._language_queue.pop(0)
            if key in self._stale_tabs:
                try:
                    self._refresh_stale_tab(key)
                except Exception:
                    get_logger(__name__).exception("Failed to rebuild tab %s for the new language", key)
                    self._stale_tabs.discard(key)
                break
        if self._language_queue:
            self.after(20, self._language_step)
            return
        self._update_visibility()
        splash, self._language_splash = self._language_splash, None
        if splash is not None:
            try:
                splash.fade_out()
            except Exception:
                pass

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
        placed = key in self._placed
        old.place_forget()
        old.destroy()  # <Destroy> вкладки зупиняє її фонові потоки
        self._placed.discard(key)
        frame = frame_cls(self.content_area)
        self.tab_frames[key] = frame
        self.shell.on_tab_rebuilt(key, frame)
        if placed or key == self._current_tab:
            self._place_tab(key)
            if key == self._current_tab:
                frame.lift()
            else:
                frame.lower()
            self._update_visibility()
            if fraction and key == self._current_tab:
                self.after(150, lambda: _restore_scroll(frame, fraction))

    # --------------------------------------------- «паркування» вкладок і прогрів

    def _place_tab(self, key: str) -> None:
        """Розмістити вкладку (мапінг ~200 мс); повторно — нічого. Лишається розміщеною під
        поточною, тож наступний показ — лише lift() (0 мс)."""
        if key not in self._placed:
            self.tab_frames[key].place(relx=0, rely=0, y=0, relwidth=1, relheight=1)
            self._placed.add(key)

    def _on_root_configure(self, event) -> None:
        """Розмір вікна змінився: запарковані вкладки знімаємо з розкладки (інакше кожна з 9
        перелаштовується при перетягуванні краю); перепрогрів — коли зміни вщухнуть."""
        if event.widget is not self:
            return
        size = (event.width, event.height)
        if size == self._last_size:
            return
        self._last_size = size
        for key in list(self._placed):
            if key != self._current_tab:
                self.tab_frames[key].place_forget()
                self._placed.discard(key)
        if self._warm_job is not None:
            self.after_cancel(self._warm_job)
            self._warm_job = None
        self._schedule_warmup(delay=1500)

    def _schedule_warmup(self, delay: int = _WARMUP_FIRST_MS) -> None:
        if self._warm_job is None and (self._stale_tabs or any(k not in self._placed for k in self.tab_frames)):
            self._warm_job = self.after(delay, self._warmup_step)

    def _warmup_step(self) -> None:
        """Фоновий прогрів: по одній вкладці за раз розмістити під поточною (lower), щоб
        перший показ не коштував мапінгу. Пропускається, поки вікно сховане або йде зміна мови."""
        self._warm_job = None
        try:
            hidden = self.state() in ("iconic", "withdrawn")
        except Exception:
            hidden = True
        if hidden or self._language_queue:
            return  # повернемось, коли вікно знову покажуть (_update_visibility)
        for key, _label, _cls in TABS:
            if key == self._current_tab or (key in self._placed and key not in self._stale_tabs):
                continue
            if key in self._stale_tabs:
                busy = getattr(self.tab_frames[key], "is_busy", None)
                if busy is not None and busy():
                    continue
                self._stale_tabs.discard(key)
                self._placed.add(key)  # поки лишалась розміщеною (запаркована) — _rebuild_tab розмістить і опустить
                self._rebuild_tab(key)
                break
            self._place_tab(key)
            self.tab_frames[key].lower()
            break
        self._schedule_warmup(delay=_WARMUP_STEP_MS)

    def select_tab(self, key: str) -> None:
        """Публічна навігація для кнопок з інших вкладок (напр. підказки, звіт)."""
        self._select_tab(key)

    def _select_tab(self, key: str):
        Dropdown.close_all()
        if key == self._current_tab:
            return
        self._current_tab = key
        splash = None
        if key in self._stale_tabs and self._language_splash is None:
            # вкладка ще з попередньою мовою: перебудова ~0.3–0.5 с — під заставкою, а не «ламанням»
            try:
                splash = LanguageSplash(self)
            except Exception:
                get_logger(__name__).exception("Could not show the language splash")
        if key in self._stale_tabs:
            self._refresh_stale_tab(key)  # перебудова новою мовою (розмістить сама)

        frame = self.tab_frames[key]
        self._place_tab(key)  # першого разу ~200 мс; запарковану — лише піднімаємо
        frame.lift()
        self._update_visibility()
        if splash is not None:
            splash.fade_out()

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
