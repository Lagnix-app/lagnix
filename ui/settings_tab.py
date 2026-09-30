"""Вкладка «Налаштування»: Загальні, Монітор, Звуки, Ігровий режим, Інтерфейс,
Дані, Про програму. Усе зберігається в settings.json одразу при зміні
(core/settings.py) і застосовується без перезапуску — прапорці анімацій/звуку
читаються живими модулями (ui/theme.py, core/sounds.py) на льоту.

Компактна розкладка: сегментовані перемикачі — групою ~300–400 px, повзунки —
до 400 px зі значенням праворуч; на широкому вікні картки йдуть у дві колонки."""

import os
import threading
from tkinter import messagebox

import customtkinter as ctk

from core import app_catalog, game_sessions, launch_on_windows, pawnio, sensors, sounds
from core import network as network_core
from core import tray as tray_core
from core.app_info import APP_DESCRIPTION, APP_VERSION
from core.logging_setup import LOG_PATH
from core.settings import load_settings, reset_to_defaults, update_setting
from core.tweaks import BACKUPS_DIR
from ui import bg, theme
from ui.widgets import confirm_dialog
from ui.widgets import robot as robot_view
from ui.widgets.canvas_list import Tooltip

_UPDATE_INTERVAL_LABELS = {0.5: "0.5 с", 1.0: "1 с", 2.0: "2 с"}
_STARTUP_TAB_LABELS = {"last": "Остання відкрита", "monitor": "Монітор"}
_CLOSE_ACTION_LABELS = {"tray": "Згортати в трей", "exit": "Закривати програму"}
_LANGUAGES = {"uk": "Українська"}

_SLIDER_WIDTH = 380            # повзунки — не на всю ширину
_SEGMENT_WIDTH_PER_ITEM = 150  # група сегментів ~300–400 px
_TWO_COLUMNS_MIN_PX = 1080     # ширина області, з якої картки йдуть у дві колонки
_WRAP = 460


class SettingsTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self, text="Налаштування", font=theme.font_title()).grid(
            row=0, column=0, padx=theme.PAD_L, pady=(theme.PAD_L, theme.PAD_M), sticky="w"
        )

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=(theme.PAD_S, 0), pady=(0, theme.PAD_M))
        self.scroll.grid_columnconfigure((0, 1), weight=1, uniform="cols")

        self._cards: list[ctk.CTkFrame] = []
        self._columns = 0
        self._layout_job = None
        self._visible = False
        self._pawnio_task = None
        bg.ensure_pump(self)
        self._build_all_cards()
        self.scroll.bind("<Configure>", self._on_scroll_configure, add="+")

    # ------------------------------------------------------------ каркас

    def _build_all_cards(self) -> None:
        self._cards = []
        self._columns = 0
        self._build_general_card()
        self._build_monitor_card()
        self._build_sound_card()
        self._build_game_mode_card()
        self._build_interface_card()
        self._build_data_card()
        self._build_about_card()
        self._layout_cards()

    def _rebuild(self) -> None:
        self._column_frames = []
        for child in self.scroll.winfo_children():
            child.destroy()
        self._build_all_cards()

    def _on_scroll_configure(self, _event=None) -> None:
        if self._layout_job is None:
            self._layout_job = self.after(60, self._layout_cards)

    def _layout_cards(self) -> None:
        """Одна колонка на вузькому вікні, дві — на широкому. У двох колонках картки
        розкладаються за висотою (у коротшу колонку), щоб не було великих дір."""
        self._layout_job = None
        width = self.scroll.winfo_width() / max(self._get_widget_scaling(), 0.5)
        columns = 2 if width >= _TWO_COLUMNS_MIN_PX else 1
        if columns == self._columns:
            return
        self._columns = columns
        for column in getattr(self, "_column_frames", []):
            column.destroy()
        self._column_frames = []
        for i in range(columns):
            column = ctk.CTkFrame(self.scroll, fg_color="transparent")
            column.grid(row=0, column=i, columnspan=2 if columns == 1 else 1, sticky="nsew")
            self._column_frames.append(column)
        heights = [0] * columns
        for card in self._cards:
            index = heights.index(min(heights))
            card.pack_forget()
            card.pack(in_=self._column_frames[index], fill="x", padx=theme.PAD_S, pady=(0, theme.PAD_M))
            card.lift()  # поверх рамки колонки (яку створено пізніше за картку)
            heights[index] += card.winfo_reqheight()

    def on_visibility_changed(self, visible: bool) -> None:
        self._visible = visible
        robot = getattr(self, "_about_robot", None)
        if robot is not None and robot.winfo_exists():
            robot.set_running(visible)
        if visible:
            self._refresh_pawnio_status()

    # ------------------------------------------------------------ helpers

    def _card(self, title: str, subtitle: str | None = None) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self.scroll, corner_radius=theme.CORNER_RADIUS, fg_color=theme.BG_PANEL)
        self._cards.append(card)
        ctk.CTkLabel(card, text=title, font=theme.font_header()).pack(
            padx=theme.PAD_M, pady=(theme.PAD_M, 4 if subtitle else 10), anchor="w"
        )
        if subtitle:
            self._hint(card, subtitle).pack(padx=theme.PAD_M, pady=(0, 10), anchor="w")
        return card

    def _hint(self, parent, text: str, color=theme.TEXT_DIM) -> ctk.CTkLabel:
        return ctk.CTkLabel(parent, text=text, text_color=color, font=theme.font_small(),
                            wraplength=_WRAP, justify="left", anchor="w")

    def _secondary_button(self, parent, text: str, command, **options) -> ctk.CTkButton:
        """Другорядна дія: контур без заливки."""
        style = {"fg_color": "transparent", "border_width": 1, "border_color": theme.BORDER,
                 "hover_color": theme.BG_PANEL_LIGHT, "text_color": theme.TEXT_MAIN}
        style.update(options)
        return ctk.CTkButton(parent, text=text, height=30, corner_radius=8, width=10, command=command, **style)

    def _choice_row(self, card, label_text: str, value_to_label: dict, current_value, on_select):
        label_to_value = {label: value for value, label in value_to_label.items()}
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=theme.PAD_M, pady=(0, 12))
        ctk.CTkLabel(row, text=label_text, font=theme.font_body()).pack(anchor="w")
        width = min(400, max(300, _SEGMENT_WIDTH_PER_ITEM * len(value_to_label)))
        seg = ctk.CTkSegmentedButton(
            row, values=list(value_to_label.values()), width=width, dynamic_resizing=False,
            command=lambda label: on_select(label_to_value[label]),
        )
        seg.set(value_to_label.get(current_value, next(iter(value_to_label.values()))))
        seg.pack(anchor="w", pady=(6, 0))
        return seg

    def _slider_row(self, card, label_text: str, from_, to, steps, value, value_text: str, on_change,
                    on_release=None):
        """Повзунок до ~400 px, значення — праворуч від нього."""
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=theme.PAD_M, pady=(0, 12))
        ctk.CTkLabel(row, text=label_text, font=theme.font_body()).pack(anchor="w")
        line = ctk.CTkFrame(row, fg_color="transparent")
        line.pack(anchor="w", pady=(6, 0))
        slider = ctk.CTkSlider(line, from_=from_, to=to, number_of_steps=steps, width=_SLIDER_WIDTH,
                               command=on_change)
        slider.set(value)
        slider.pack(side="left")
        value_label = ctk.CTkLabel(line, text=value_text, text_color=theme.TEXT_DIM, anchor="w",
                                   font=theme.font_body())
        value_label.pack(side="left", padx=(12, 0))
        if on_release is not None:
            slider.bind("<ButtonRelease-1>", on_release, add="+")
        return slider, value_label

    def _switch(self, parent, text: str, value: bool, command, hint: str | None = None):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.pack(fill="x", padx=theme.PAD_M, pady=(0, 12))
        var = ctk.BooleanVar(value=value)
        ctk.CTkSwitch(box, text=text, variable=var, command=command).pack(anchor="w")
        if hint:
            self._hint(box, hint).pack(anchor="w", pady=(2, 0))
        return var

    # ------------------------------------------------------------ загальні

    def _build_general_card(self) -> None:
        card = self._card("Загальні")
        settings = load_settings()

        self._launch_var = self._switch(
            card, "Запускати разом з Windows", launch_on_windows.is_enabled(), self._on_toggle_launch_with_windows,
            "Стартує мінімізованою в трей разом із входом у Windows.",
        )

        close_labels = dict(_CLOSE_ACTION_LABELS)
        if not tray_core.is_available():
            close_labels.pop("tray", None)
        self._choice_row(card, "При закритті вікна", close_labels,
                         settings.get("close_action", "exit"), self._on_close_action_change)
        if not tray_core.is_available():
            self._hint(card, "Трей недоступний у цьому оточенні (немає pystray) — програма завжди закривається.",
                       theme.WARNING).pack(padx=theme.PAD_M, pady=(0, 12), anchor="w")

        self._choice_row(card, "Вкладка при запуску", _STARTUP_TAB_LABELS,
                         settings.get("startup_tab_mode", "last"), self._on_startup_tab_change)

    def _on_toggle_launch_with_windows(self) -> None:
        enabled = self._launch_var.get()
        success, error = launch_on_windows.set_enabled(enabled)
        if not success:
            self._launch_var.set(not enabled)
            messagebox.showerror("Помилка", f"Не вдалося змінити автозапуск: {error}", parent=self)
            return
        sounds.play_click()

    def _on_close_action_change(self, value: str) -> None:
        update_setting("close_action", value)
        sounds.play_click()

    def _on_startup_tab_change(self, value: str) -> None:
        update_setting("startup_tab_mode", value)
        sounds.play_click()

    # ------------------------------------------------------------- монітор

    def _build_monitor_card(self) -> None:
        card = self._card("Монітор")
        settings = load_settings()

        self._choice_row(card, "Інтервал оновлення", _UPDATE_INTERVAL_LABELS,
                         settings.get("monitor_update_interval_s", 1.0), self._on_interval_change)

        threshold = settings.get("temp_threshold_c", 85)
        self._threshold_slider, self._threshold_value_label = self._slider_row(
            card, "Поріг попередження про температуру CPU/GPU", 60, 100, 40, threshold,
            f"{round(threshold)}°C", self._on_threshold_change,
        )

        self._sensors_var = self._switch(
            card, "Розширені датчики (температура CPU)", settings.get("advanced_sensors_enabled", True),
            self._on_toggle_sensors,
            "Потрібні для показу температури процесора. Якщо вимкнути — драйвер датчиків не завантажується.",
        )

        driver = ctk.CTkFrame(card, fg_color=theme.BG_PANEL_LIGHT, corner_radius=10)
        driver.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))
        top = ctk.CTkFrame(driver, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(top, text="Драйвер датчиків PawnIO", font=theme.font_body()).pack(side="left")
        self._pawnio_status = ctk.CTkLabel(top, text="перевіряю…", font=theme.font_body(),
                                           text_color=theme.TEXT_DIM)
        self._pawnio_status.pack(side="right")
        self._pawnio_hint = self._hint(
            driver, "Без нього температура CPU показується як «Недоступно». Офіційний підписаний драйвер "
                    "(pawnio.eu), встановлюється лише за вашою згодою.")
        self._pawnio_hint.pack(fill="x", padx=12, pady=(0, 8))
        self._pawnio_button = ctk.CTkButton(driver, text="Встановити драйвер датчиків", height=30,
                                            corner_radius=8, width=10, command=self._on_install_pawnio)
        self._refresh_pawnio_status()

    def _refresh_pawnio_status(self) -> None:
        if not hasattr(self, "_pawnio_status") or not self._pawnio_status.winfo_exists():
            return
        if self._pawnio_task is not None and not self._pawnio_task.finished:
            return
        self._pawnio_task = bg.run_task(self, "Налаштування: статус PawnIO", pawnio.status,
                                        self._apply_pawnio_status, lambda _e: self._apply_pawnio_status(None),
                                        timeout=20)

    def _apply_pawnio_status(self, status) -> None:
        if not self._pawnio_status.winfo_exists():
            return
        if status is None:
            self._pawnio_status.configure(text="не вдалося перевірити", text_color=theme.WARNING)
            return
        if status["installed"]:
            self._pawnio_status.configure(text="● Встановлено", text_color=theme.ACCENT_GREEN)
            self._pawnio_button.pack_forget()
        else:
            self._pawnio_status.configure(text="● Не встановлено", text_color=theme.WARNING)
            if not self._pawnio_button.winfo_manager():
                self._pawnio_button.pack(anchor="w", padx=12, pady=(0, 12))

    def _on_install_pawnio(self) -> None:
        if not confirm_dialog.ask(
            self, "Встановити драйвер датчиків?",
            "PulseFPS завантажить офіційний інсталятор PawnIO (github.com/namazso/PawnIO.Setup — посилання з "
            "pawnio.eu), перевірить його цифровий підпис і відкриє майстер встановлення. У майстрі оберіть "
            "звичайну (підписану) редакцію.\n\nДрайвер працює на рівні ядра Windows; видалити його можна "
            "через «Установлені програми».",
            "Завантажити й встановити",
        ):
            return
        reason = "Налаштування → «Встановити драйвер датчиків»"
        self._pawnio_button.configure(state="disabled", text="Завантажую й перевіряю підпис…")
        bg.run_task(self, "Налаштування: встановлення PawnIO",
                    lambda: pawnio.run_installer(pawnio.download_and_verify(reason), reason),
                    self._on_pawnio_installed, self._on_pawnio_failed, timeout=30 * 60)

    def _on_pawnio_installed(self, _code) -> None:
        self._pawnio_button.configure(state="normal", text="Встановити драйвер датчиків")
        status = pawnio.status()
        self._apply_pawnio_status(status)
        if status["installed"] and load_settings().get("advanced_sensors_enabled", True):
            # перезапуск датчиків, щоб LibreHardwareMonitor підхопив драйвер
            threading.Thread(target=lambda: (sensors.stop(), sensors.start()), daemon=True).start()
            messagebox.showinfo("Драйвер датчиків", "PawnIO встановлено — температура CPU з'явиться "
                                                   "на «Моніторі» за кілька секунд.", parent=self)

    def _on_pawnio_failed(self, exc) -> None:
        self._pawnio_button.configure(state="normal", text="Встановити драйвер датчиків")
        messagebox.showerror("Драйвер датчиків", f"Не вдалося встановити PawnIO: {bg.error_text(exc)}",
                             parent=self)

    def _on_interval_change(self, value: float) -> None:
        update_setting("monitor_update_interval_s", value)
        sounds.play_click()

    def _on_toggle_sensors(self) -> None:
        enabled = self._sensors_var.get()
        update_setting("advanced_sensors_enabled", enabled)
        threading.Thread(target=sensors.set_enabled, args=(enabled,), daemon=True).start()
        sounds.play_click()

    def _on_threshold_change(self, value: float) -> None:
        value = round(value)
        update_setting("temp_threshold_c", value)
        self._threshold_value_label.configure(text=f"{value}°C")

    # --------------------------------------------------------------- звуки

    def _build_sound_card(self) -> None:
        card = self._card("Звуки", "Тихі звукові підказки при наведенні, кліку, успіху й помилках.")
        self._sound_var = self._switch(card, "Звуки увімкнені", sounds.is_enabled(), self._on_toggle_sounds)
        self._volume_slider, self._volume_value_label = self._slider_row(
            card, "Загальна гучність", 0, 1, 20, sounds.get_volume(), "", self._on_volume_change,
            self._on_volume_release,
        )
        self._hover_slider, self._hover_value_label = self._slider_row(
            card, "Звук наведення (частка від загальної)", 0, 1, 20, sounds.get_hover_ratio(), "",
            self._on_hover_change, self._on_hover_release,
        )
        self._test_button = self._secondary_button(card, "Тест звуку", self._on_test_sound)
        self._test_button.pack(padx=theme.PAD_M, pady=(0, theme.PAD_M), anchor="w")
        self._update_volume_labels()
        self._update_volume_state()

    def _update_volume_labels(self) -> None:
        volume, ratio = sounds.get_volume(), sounds.get_hover_ratio()
        muted = volume <= 0
        self._volume_value_label.configure(text="Звук вимкнено" if muted else f"{round(volume * 100)}%",
                                           text_color=theme.WARNING if muted else theme.TEXT_DIM)
        if muted or ratio <= 0:
            hover_text = "Звук вимкнено"
        else:
            hover_text = f"{round(ratio * 100)}% від загальної"
        self._hover_value_label.configure(text=hover_text)

    def _on_toggle_sounds(self) -> None:
        enabled = self._sound_var.get()
        sounds.set_enabled(enabled)
        self._update_volume_state()
        if enabled:
            sounds.play_click()

    def _update_volume_state(self) -> None:
        state = "normal" if self._sound_var.get() else "disabled"
        self._volume_slider.configure(state=state)
        self._hover_slider.configure(state=state)

    def _on_volume_change(self, value: float) -> None:
        sounds.set_volume(value, persist=False)  # збережеться при відпусканні
        self._update_volume_labels()

    def _on_volume_release(self, _event) -> None:
        sounds.set_volume(self._volume_slider.get())
        self._update_volume_labels()
        if self._sound_var.get():
            sounds.play_click()

    def _on_hover_change(self, value: float) -> None:
        sounds.set_hover_ratio(value, persist=False)
        self._update_volume_labels()

    def _on_hover_release(self, _event) -> None:
        sounds.set_hover_ratio(self._hover_slider.get())
        self._update_volume_labels()
        if self._sound_var.get():
            sounds.play_hover(force=True)

    def _on_test_sound(self) -> None:
        """Програє всі звуки по черзі (з паузами, щоб було чутно кожен окремо) —
        незалежно від перемикача, щоб звуки можна було прослухати перед увімкненням."""
        self._test_button.configure(state="disabled", text="Відтворення...")
        sequence = (sounds.play_hover, sounds.play_hover, sounds.play_click, sounds.play_success, sounds.play_error)
        delay = 0
        for play_fn in sequence:
            self.after(delay, lambda fn=play_fn: fn(force=True))
            delay += 400
        self.after(delay + 200, self._on_test_sound_done)

    def _on_test_sound_done(self) -> None:
        if self._test_button.winfo_exists():
            self._test_button.configure(state="normal", text="Тест звуку")

    # -------------------------------------------------------- ігровий режим

    def _build_game_mode_card(self) -> None:
        card = self._card("Ігровий режим")
        settings = load_settings()
        self._choice_row(
            card, "Рівень за замовчуванням", app_catalog.LEVEL_LABELS,
            settings.get("game_mode_default_level", app_catalog.DEFAULT_LEVEL), self._on_default_level,
        )
        self._hint(card, "Для профілів, у яких рівень ще не обирали на вкладці «Ігровий режим».").pack(
            padx=theme.PAD_M, pady=(0, 12), anchor="w")
        self._toast_var = self._switch(
            card, "Сповіщення при автоувімкненні", settings.get("game_mode_auto_toast", True), self._on_toggle_toast,
            "«Ігровий режим увімкнено для <гра>» з кнопкою «Скасувати» (5 с). Якщо вимкнути — режим "
            "вмикається одразу; програми автоматично однаково не закриваються.",
        )

    def _on_default_level(self, level: str) -> None:
        update_setting("game_mode_default_level", level)
        sounds.play_click()
        game_tab = getattr(self.winfo_toplevel(), "tab_frames", {}).get("game_mode")
        if game_tab is not None and hasattr(game_tab, "_render"):
            game_tab._render()

    def _on_toggle_toast(self) -> None:
        update_setting("game_mode_auto_toast", bool(self._toast_var.get()))
        sounds.play_click()

    # ---------------------------------------------------------- інтерфейс

    def _build_interface_card(self) -> None:
        card = self._card("Інтерфейс", "Вимкніть анімації, якщо інтерфейс гальмує на слабкому ПК.")
        settings = load_settings()
        self._anim_var = self._switch(card, "Анімації інтерфейсу", settings.get("animations_enabled", True),
                                      self._on_toggle_animations)
        self._robot_anim_var = self._switch(card, "Анімація робота", settings.get("robot_animation_enabled", True),
                                            self._on_toggle_robot_animation)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))
        ctk.CTkLabel(row, text="Мова", font=theme.font_body()).pack(anchor="w")
        self._language_menu = ctk.CTkOptionMenu(
            row, values=list(_LANGUAGES.values()), width=220, height=30, corner_radius=8,
            fg_color=theme.BG_PANEL_LIGHT, button_color=theme.BG_PANEL_LIGHT, button_hover_color=theme.BORDER,
            dropdown_fg_color=theme.BG_PANEL, dropdown_hover_color=theme.BORDER, text_color=theme.TEXT_MAIN,
            command=self._on_language,
        )
        self._language_menu.set(_LANGUAGES.get(settings.get("language", "uk"), "Українська"))
        self._language_menu.pack(anchor="w", pady=(6, 0))
        self._hint(row, "Інші мови з'являться згодом.").pack(anchor="w", pady=(2, 0))

    def _on_language(self, label: str) -> None:
        code = next((c for c, name in _LANGUAGES.items() if name == label), "uk")
        update_setting("language", code)

    def _on_toggle_animations(self) -> None:
        enabled = self._anim_var.get()
        theme.set_animations_enabled(enabled)
        update_setting("animations_enabled", enabled)

    def _on_toggle_robot_animation(self) -> None:
        enabled = self._robot_anim_var.get()
        theme.set_robot_animation_enabled(enabled)
        update_setting("robot_animation_enabled", enabled)

    # --------------------------------------------------------------- дані

    def _build_data_card(self) -> None:
        card = self._card("Дані")
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=theme.PAD_M, pady=(0, 12))
        self._secondary_button(buttons, "Резервні копії твіків", self._open_backups_folder).pack(
            side="left", padx=(0, 8))
        self._secondary_button(buttons, "Журнал (logs.txt)", self._open_logs_file).pack(side="left")

        danger = ctk.CTkFrame(card, fg_color="transparent")
        danger.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))
        self._secondary_button(danger, "Очистити історію", self._confirm_clear_history).pack(
            side="left", padx=(0, 8))
        self._secondary_button(danger, "Скинути налаштування", self._confirm_reset_settings).pack(side="left")

    def _open_backups_folder(self) -> None:
        os.makedirs(BACKUPS_DIR, exist_ok=True)
        os.startfile(BACKUPS_DIR)

    def _open_logs_file(self) -> None:
        if not os.path.exists(LOG_PATH):
            open(LOG_PATH, "a", encoding="utf-8").close()
        os.startfile(LOG_PATH)

    def _confirm_clear_history(self) -> None:
        tests = len(network_core.load_test_history())
        sessions = len(game_sessions.load_sessions())
        if not confirm_dialog.ask(
            self, "Очистити історію?",
            f"Буде видалено історію тестів мережі ({tests}) і підсумки ігрових сесій ({sessions}).\n\n"
            "Налаштування, резервні копії твіків і список ігор не зміняться.",
            "Очистити", danger=True,
        ):
            return
        network_core.clear_test_history()
        game_sessions.clear_sessions()
        from core.logging_setup import get_audit_logger
        get_audit_logger().info("Очищено історію: тести мережі (%d), ігрові сесії (%d) — причина: "
                                "Налаштування → «Очистити історію»", tests, sessions)
        frames = getattr(self.winfo_toplevel(), "tab_frames", {})
        for key, method in (("game_mode", "_render_sessions"), ("network", "reload_history")):
            hook = getattr(frames.get(key), method, None)
            if hook is not None:
                hook()
        sounds.play_success()

    def _confirm_reset_settings(self) -> None:
        if not confirm_dialog.ask(
            self, "Скинути налаштування?",
            "Загальні налаштування (звук, інтерфейс, поріг температури, Ігровий режим тощо) повернуться до "
            "типових значень.\n\nЗбережені початкові значення твіків реєстру, історія та вимкнені записи "
            "автозапуску НЕ зміняться.",
            "Скинути", danger=True,
        ):
            return
        reset_to_defaults()
        self._apply_reset_side_effects()
        self._rebuild()

    def _apply_reset_side_effects(self) -> None:
        defaults = load_settings()
        theme.set_animations_enabled(defaults.get("animations_enabled", True))
        theme.set_robot_animation_enabled(defaults.get("robot_animation_enabled", True))
        sounds.set_enabled(defaults.get("sounds_enabled", True))
        threading.Thread(
            target=sensors.set_enabled, args=(defaults.get("advanced_sensors_enabled", True),), daemon=True,
        ).start()
        sounds.reset_volumes(defaults.get("sounds_volume", 0.25), defaults.get("sounds_hover_ratio", 0.5))

    # --------------------------------------------------------- про програму

    def _build_about_card(self) -> None:
        card = self._card("Про програму")
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=theme.PAD_M, pady=(0, 12))

        self._about_robot = robot_view.RobotView(row, size=132, mood=robot_view.HAPPY, bg=theme.BG_PANEL)
        self._about_robot.pack(side="left", padx=(0, 18))
        self._about_robot.set_running(self._visible)

        text_col = ctk.CTkFrame(row, fg_color="transparent")
        text_col.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(text_col, text=f"PulseFPS  ·  версія {APP_VERSION}", font=theme.font_header()).pack(anchor="w")
        self._hint(text_col, APP_DESCRIPTION).pack(anchor="w", pady=(4, 0))
        self._hint(text_col, "Датчики температури: LibreHardwareMonitor (MPL-2.0) — github.com/LibreHardwareMonitor. "
                             "Повний перелік ліцензій — файл THIRD_PARTY_LICENSES.").pack(anchor="w", pady=(6, 0))

        links = ctk.CTkFrame(card, fg_color="transparent")
        links.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))
        for text in ("GitHub", "Повідомити про помилку", "Підтримати"):
            button = self._secondary_button(links, text, None, state="disabled", text_color_disabled=theme.TEXT_DIM)
            button.pack(side="left", padx=(0, 8))
            tooltip = Tooltip(button)
            button.bind("<Enter>", lambda _e, t=tooltip: t.schedule("Скоро"), add="+")
            button.bind("<Leave>", lambda _e, t=tooltip: t.hide(), add="+")
