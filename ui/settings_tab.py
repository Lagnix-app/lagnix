"""Вкладка «Налаштування» — звук інтерфейсу (інші розділи ще заплановані)."""

import customtkinter as ctk

from core import sounds
from ui import theme


class SettingsTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        ctk.CTkLabel(self, text="Налаштування", font=theme.font_title()).pack(
            padx=theme.PAD_L, pady=(theme.PAD_L, theme.PAD_M), anchor="w"
        )

        self._build_sound_card()

    def _build_sound_card(self) -> None:
        card = ctk.CTkFrame(self, corner_radius=theme.CORNER_RADIUS)
        card.pack(fill="x", padx=theme.PAD_L, pady=(0, theme.PAD_M))

        ctk.CTkLabel(card, text="Звук", font=theme.font_header()).pack(
            padx=theme.PAD_M, pady=(theme.PAD_M, 4), anchor="w"
        )
        ctk.CTkLabel(
            card,
            text="Тихі звукові підказки при наведенні, кліку, успіху й помилках.",
            text_color=theme.TEXT_DIM,
            font=theme.font_small(),
        ).pack(padx=theme.PAD_M, pady=(0, theme.PAD_M), anchor="w")

        switch_row = ctk.CTkFrame(card, fg_color="transparent")
        switch_row.pack(fill="x", padx=theme.PAD_M, pady=(0, 10))

        self._sound_var = ctk.BooleanVar(value=sounds.is_enabled())
        self._switch = ctk.CTkSwitch(
            switch_row, text="Звуки увімкнені", variable=self._sound_var,
            command=self._on_toggle_sounds,
        )
        self._switch.pack(anchor="w")

        volume_row = ctk.CTkFrame(card, fg_color="transparent")
        volume_row.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))

        header = ctk.CTkFrame(volume_row, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(header, text="Гучність", font=theme.font_body()).pack(side="left")
        self._volume_value_label = ctk.CTkLabel(
            header, text=f"{round(sounds.get_volume() * 100)}%", text_color=theme.TEXT_DIM,
        )
        self._volume_value_label.pack(side="right")

        self._volume_slider = ctk.CTkSlider(
            volume_row, from_=0, to=1, number_of_steps=20,
            command=self._on_volume_change,
        )
        self._volume_slider.set(sounds.get_volume())
        self._volume_slider.pack(fill="x", pady=(6, 0))
        self._volume_slider.bind("<ButtonRelease-1>", self._on_volume_release, add="+")

        self._test_button = ctk.CTkButton(
            card, text="Тест звуку", width=140, command=self._on_test_sound,
        )
        self._test_button.pack(padx=theme.PAD_M, pady=(0, theme.PAD_M), anchor="w")

        self._update_volume_state()

    def _on_toggle_sounds(self) -> None:
        enabled = self._sound_var.get()
        sounds.set_enabled(enabled)
        self._update_volume_state()
        if enabled:
            sounds.play_click()

    def _update_volume_state(self) -> None:
        state = "normal" if self._sound_var.get() else "disabled"
        self._volume_slider.configure(state=state)

    def _on_volume_change(self, value: float) -> None:
        sounds.set_volume(value)
        self._volume_value_label.configure(text=f"{round(value * 100)}%")

    def _on_volume_release(self, _event) -> None:
        if self._sound_var.get():
            sounds.play_click()

    def _on_test_sound(self) -> None:
        """Програє всі 4 звуки по черзі (з паузами, щоб було чутно кожен
        окремо) — незалежно від перемикача, щоб можна було "прослухати"
        звуки перед тим, як їх вмикати."""
        self._test_button.configure(state="disabled", text="Відтворення...")
        sequence = (sounds.play_hover, sounds.play_click, sounds.play_success, sounds.play_error)
        delay = 0
        for play_fn in sequence:
            self.after(delay, lambda fn=play_fn: fn(force=True))
            delay += 450
        self.after(delay + 200, self._on_test_sound_done)

    def _on_test_sound_done(self) -> None:
        self._test_button.configure(state="normal", text="Тест звуку")
