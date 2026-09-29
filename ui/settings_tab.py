"""Вкладка «Налаштування»: Загальні, Монітор, Звуки, Інтерфейс, Дані, Про
програму. Усе зберігається в settings.json одразу при зміні (core/settings.py)
і застосовується без перезапуску — прапорці анімацій/звуку читаються
живими модулями (ui/theme.py, core/sounds.py) на льоту, а не лише при
наступному старті."""

import os
import threading
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image

from core import launch_on_windows
from core import sensors, sounds
from core import tray as tray_core
from core.app_info import APP_DESCRIPTION, APP_VERSION
from core.logging_setup import LOG_PATH
from core.settings import load_settings, reset_to_defaults, update_setting
from core.tweaks import BACKUPS_DIR
from ui import theme

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGO_PATH = os.path.join(_ROOT, "assets", "pulsefps-icon-256.png")

_UPDATE_INTERVAL_LABELS = {0.5: "0.5 с", 1.0: "1 с", 2.0: "2 с"}

_STARTUP_TAB_LABELS = {"last": "Остання відкрита", "monitor": "Монітор"}

_CLOSE_ACTION_LABELS = {"tray": "Згортати в трей", "exit": "Закривати програму"}


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
        self.scroll.grid_columnconfigure(0, weight=1)

        self._build_all_cards()

    def _build_all_cards(self) -> None:
        self._build_general_card()
        self._build_monitor_card()
        self._build_sound_card()
        self._build_interface_card()
        self._build_data_card()
        self._build_about_card()

    def _rebuild(self) -> None:
        for child in self.scroll.winfo_children():
            child.destroy()
        self._build_all_cards()

    # ------------------------------------------------------------ helpers

    def _card(self, title: str, subtitle: str | None = None) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self.scroll, corner_radius=theme.CORNER_RADIUS)
        card.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))
        ctk.CTkLabel(card, text=title, font=theme.font_header()).pack(
            padx=theme.PAD_M, pady=(theme.PAD_M, 4 if subtitle else theme.PAD_M), anchor="w"
        )
        if subtitle:
            ctk.CTkLabel(
                card, text=subtitle, text_color=theme.TEXT_DIM, font=theme.font_small(),
                wraplength=640, justify="left",
            ).pack(padx=theme.PAD_M, pady=(0, theme.PAD_M), anchor="w")
        return card

    def _choice_row(self, card, label_text: str, value_to_label: dict, current_value, on_select):
        label_to_value = {label: value for value, label in value_to_label.items()}

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))
        ctk.CTkLabel(row, text=label_text, font=theme.font_body()).pack(anchor="w")

        seg = ctk.CTkSegmentedButton(
            row, values=list(value_to_label.values()),
            command=lambda label: on_select(label_to_value[label]),
        )
        seg.set(value_to_label.get(current_value, next(iter(value_to_label.values()))))
        seg.pack(fill="x", pady=(6, 0))
        return seg

    # ------------------------------------------------------------ загальні

    def _build_general_card(self) -> None:
        card = self._card("Загальні")
        settings = load_settings()

        launch_col = ctk.CTkFrame(card, fg_color="transparent")
        launch_col.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))
        self._launch_var = ctk.BooleanVar(value=launch_on_windows.is_enabled())
        ctk.CTkSwitch(
            launch_col, text="Запускати разом з Windows", variable=self._launch_var,
            command=self._on_toggle_launch_with_windows,
        ).pack(anchor="w")
        ctk.CTkLabel(
            launch_col, text="Стартує мінімізованою в трей разом із входом у Windows.",
            text_color=theme.TEXT_DIM, font=theme.font_small(),
        ).pack(anchor="w", pady=(2, 0))

        close_labels = dict(_CLOSE_ACTION_LABELS)
        if not tray_core.is_available():
            close_labels.pop("tray", None)
        self._choice_row(
            card, "При закритті вікна", close_labels,
            settings.get("close_action", "exit"), self._on_close_action_change,
        )
        if not tray_core.is_available():
            ctk.CTkLabel(
                card,
                text="Трей недоступний у цьому оточенні (немає pystray) — програма завжди закривається.",
                text_color=theme.WARNING, font=theme.font_small(), wraplength=640, justify="left",
            ).pack(padx=theme.PAD_M, pady=(0, theme.PAD_M), anchor="w")

        self._choice_row(
            card, "Вкладка при запуску", _STARTUP_TAB_LABELS,
            settings.get("startup_tab_mode", "last"), self._on_startup_tab_change,
        )

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

        self._choice_row(
            card, "Інтервал оновлення", _UPDATE_INTERVAL_LABELS,
            settings.get("monitor_update_interval_s", 1.0), self._on_interval_change,
        )

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))
        header = ctk.CTkFrame(row, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(header, text="Поріг попередження про температуру CPU/GPU", font=theme.font_body()).pack(side="left")
        threshold = settings.get("temp_threshold_c", 85)
        self._threshold_value_label = ctk.CTkLabel(header, text=f"{round(threshold)}°C", text_color=theme.TEXT_DIM)
        self._threshold_value_label.pack(side="right")

        self._threshold_slider = ctk.CTkSlider(
            row, from_=60, to=100, number_of_steps=40, command=self._on_threshold_change,
        )
        self._threshold_slider.set(threshold)
        self._threshold_slider.pack(fill="x", pady=(6, 0))

        sensors_col = ctk.CTkFrame(card, fg_color="transparent")
        sensors_col.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))
        self._sensors_var = ctk.BooleanVar(value=settings.get("advanced_sensors_enabled", True))
        ctk.CTkSwitch(
            sensors_col, text="Розширені датчики (температура CPU)", variable=self._sensors_var,
            command=self._on_toggle_sensors,
        ).pack(anchor="w")
        ctk.CTkLabel(
            sensors_col,
            text="Потрібні для показу температури процесора. Якщо вимкнути — драйвер датчиків не завантажується.",
            text_color=theme.TEXT_DIM, font=theme.font_small(), wraplength=640, justify="left",
        ).pack(anchor="w", pady=(2, 0))

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

        switch_row = ctk.CTkFrame(card, fg_color="transparent")
        switch_row.pack(fill="x", padx=theme.PAD_M, pady=(0, 10))

        self._sound_var = ctk.BooleanVar(value=sounds.is_enabled())
        self._switch = ctk.CTkSwitch(
            switch_row, text="Звуки увімкнені", variable=self._sound_var,
            command=self._on_toggle_sounds,
        )
        self._switch.pack(anchor="w")

        self._volume_slider, self._volume_value_label = self._build_volume_row(
            card, "Загальна гучність", sounds.get_volume(), self._on_volume_change, self._on_volume_release,
        )
        self._hover_volume_slider, self._hover_volume_value_label = self._build_volume_row(
            card, "Звук наведення", sounds.get_hover_volume(),
            self._on_hover_volume_change, self._on_hover_volume_release,
        )

        self._test_button = ctk.CTkButton(
            card, text="Тест звуку", width=140, command=self._on_test_sound,
        )
        self._test_button.pack(padx=theme.PAD_M, pady=(0, theme.PAD_M), anchor="w")

        self._update_volume_state()

    def _build_volume_row(self, card, label_text: str, initial: float, on_change, on_release):
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))

        header = ctk.CTkFrame(row, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(header, text=label_text, font=theme.font_body()).pack(side="left")
        value_label = ctk.CTkLabel(header, text=f"{round(initial * 100)}%", text_color=theme.TEXT_DIM)
        value_label.pack(side="right")

        slider = ctk.CTkSlider(row, from_=0, to=1, number_of_steps=20, command=on_change)
        slider.set(initial)
        slider.pack(fill="x", pady=(6, 0))
        slider.bind("<ButtonRelease-1>", on_release, add="+")
        return slider, value_label

    def _on_toggle_sounds(self) -> None:
        enabled = self._sound_var.get()
        sounds.set_enabled(enabled)
        self._update_volume_state()
        if enabled:
            sounds.play_click()

    def _update_volume_state(self) -> None:
        state = "normal" if self._sound_var.get() else "disabled"
        self._volume_slider.configure(state=state)
        self._hover_volume_slider.configure(state=state)

    def _on_volume_change(self, value: float) -> None:
        sounds.set_volume(value)
        self._volume_value_label.configure(text=f"{round(value * 100)}%")

    def _on_volume_release(self, _event) -> None:
        if self._sound_var.get():
            sounds.play_click()

    def _on_hover_volume_change(self, value: float) -> None:
        sounds.set_hover_volume(value)
        self._hover_volume_value_label.configure(text=f"{round(value * 100)}%")

    def _on_hover_volume_release(self, _event) -> None:
        if self._sound_var.get():
            sounds.play_hover(force=True)

    def _on_test_sound(self) -> None:
        """Програє всі звуки по черзі (з паузами, щоб було чутно кожен
        окремо) — незалежно від перемикача, щоб можна було "прослухати"
        звуки перед тим, як їх вмикати."""
        self._test_button.configure(state="disabled", text="Відтворення...")
        sequence = (
            sounds.play_hover, sounds.play_hover,
            sounds.play_click, sounds.play_success, sounds.play_error,
        )
        delay = 0
        for play_fn in sequence:
            self.after(delay, lambda fn=play_fn: fn(force=True))
            delay += 400
        self.after(delay + 200, self._on_test_sound_done)

    def _on_test_sound_done(self) -> None:
        if self._test_button.winfo_exists():
            self._test_button.configure(state="normal", text="Тест звуку")

    # ---------------------------------------------------------- інтерфейс

    def _build_interface_card(self) -> None:
        card = self._card("Інтерфейс", "Вимкніть анімації, якщо інтерфейс гальмує на слабкому ПК.")
        settings = load_settings()

        self._anim_var = ctk.BooleanVar(value=settings.get("animations_enabled", True))
        ctk.CTkSwitch(
            card, text="Анімації інтерфейсу", variable=self._anim_var,
            command=self._on_toggle_animations,
        ).pack(padx=theme.PAD_M, pady=(0, theme.PAD_M), anchor="w")

        self._robot_anim_var = ctk.BooleanVar(value=settings.get("robot_animation_enabled", True))
        ctk.CTkSwitch(
            card, text="Анімація робота", variable=self._robot_anim_var,
            command=self._on_toggle_robot_animation,
        ).pack(padx=theme.PAD_M, pady=(0, theme.PAD_M), anchor="w")

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

        ctk.CTkButton(
            card, text="Відкрити папку з резервними копіями", command=self._open_backups_folder,
        ).pack(padx=theme.PAD_M, pady=(0, 10), anchor="w")

        ctk.CTkButton(
            card, text="Відкрити журнал помилок", command=self._open_logs_file,
        ).pack(padx=theme.PAD_M, pady=(0, 10), anchor="w")

        ctk.CTkButton(
            card, text="Скинути налаштування за замовчуванням",
            fg_color="#a8283f", hover_color=theme.ERROR, command=self._confirm_reset_settings,
        ).pack(padx=theme.PAD_M, pady=(0, theme.PAD_M), anchor="w")

    def _open_backups_folder(self) -> None:
        os.makedirs(BACKUPS_DIR, exist_ok=True)
        os.startfile(BACKUPS_DIR)

    def _open_logs_file(self) -> None:
        if not os.path.exists(LOG_PATH):
            open(LOG_PATH, "a", encoding="utf-8").close()
        os.startfile(LOG_PATH)

    def _confirm_reset_settings(self) -> None:
        confirmed = messagebox.askyesno(
            "Скинути налаштування?",
            "Загальні налаштування (звук, інтерфейс, автозапуск вікна, поріг "
            "температури тощо) повернуться до типових значень.\n\n"
            "Збережені початкові значення твіків реєстру та вимкнені записи "
            "автозапуску програм НЕ зміняться.",
            parent=self,
        )
        if not confirmed:
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
        sounds.set_volume(defaults.get("sounds_volume", 0.25))
        sounds.set_hover_volume(defaults.get("sounds_hover_volume", 0.125))

    # --------------------------------------------------------- про програму

    def _build_about_card(self) -> None:
        card = self._card("Про програму")

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))

        if os.path.exists(_LOGO_PATH):
            image = ctk.CTkImage(Image.open(_LOGO_PATH), size=(64, 64))
            ctk.CTkLabel(row, image=image, text="").pack(side="left", padx=(0, 14))

        text_col = ctk.CTkFrame(row, fg_color="transparent")
        text_col.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(
            text_col, text=f"PulseFPS  ·  версія {APP_VERSION}", font=theme.font_header(),
        ).pack(anchor="w")
        ctk.CTkLabel(
            text_col, text=APP_DESCRIPTION, text_color=theme.TEXT_DIM, font=theme.font_small(),
            wraplength=560, justify="left",
        ).pack(anchor="w", pady=(4, 0))
        ctk.CTkLabel(
            text_col,
            text="Датчики температури: LibreHardwareMonitor (MPL-2.0) — github.com/LibreHardwareMonitor. "
                 "Повний перелік ліцензій — файл THIRD_PARTY_LICENSES.",
            text_color=theme.TEXT_DIM, font=theme.font_small(), wraplength=560, justify="left",
        ).pack(anchor="w", pady=(6, 0))
