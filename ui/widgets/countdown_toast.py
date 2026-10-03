"""Сповіщення в кутку екрана з відліком: «Ігровий режим увімкнено для <гра>» +
«Скасувати». Якщо за seconds с нічого не натиснуто — on_timeout(); «Скасувати» —
on_cancel(); додаткова кнопка (extra) — її власна дія. Рівно один із колбеків."""

import tkinter as tk

import customtkinter as ctk

from ui import theme
from core.i18n import t

_WIDTH = 360
_MARGIN = 24
_TICK_MS = 100


class CountdownToast(ctk.CTkToplevel):
    def __init__(self, master, title: str, message: str, seconds: float, on_timeout, on_cancel,
                 extra: tuple[str, object] | None = None):
        super().__init__(master)
        self._seconds = seconds
        self._remaining = seconds
        self._on_timeout = on_timeout
        self._on_cancel = on_cancel
        self._done = False
        self._job = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=theme.BG_PANEL)

        body = ctk.CTkFrame(self, fg_color=theme.BG_PANEL, border_width=1, border_color=theme.ACCENT_GREEN,
                            corner_radius=12)
        body.pack(fill="both", expand=True)
        ctk.CTkLabel(body, text=title, font=theme.font_header(), text_color=theme.TEXT_MAIN, anchor="w",
                     wraplength=_WIDTH - 32, justify="left").pack(fill="x", padx=16, pady=(14, 2))
        ctk.CTkLabel(body, text=message, font=theme.font_small(), text_color=theme.TEXT_DIM, anchor="w",
                     wraplength=_WIDTH - 32, justify="left").pack(fill="x", padx=16)
        self.countdown_label = ctk.CTkLabel(body, text="", font=theme.font_small(), text_color=theme.TEXT_DIM,
                                            anchor="w")
        self.countdown_label.pack(fill="x", padx=16, pady=(6, 0))
        self.progress = ctk.CTkProgressBar(body, height=4, progress_color=theme.ACCENT_GREEN)
        self.progress.set(1.0)
        self.progress.pack(fill="x", padx=16, pady=(4, 10))

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkButton(buttons, text=t("common.cancel"), width=110, height=30, fg_color=theme.BG_PANEL_LIGHT,
                      hover_color=theme.BORDER, text_color=theme.TEXT_MAIN,
                      command=lambda: self._finish(self._on_cancel)).pack(side="right")
        if extra is not None:
            label, callback = extra
            ctk.CTkButton(buttons, text=label, height=30, fg_color="transparent", border_width=1,
                          text_color=theme.TEXT_MAIN, hover_color=theme.BORDER,
                          command=lambda: self._finish(callback)).pack(side="right", padx=(0, 8))

        self.update_idletasks()
        height = self.winfo_reqheight()
        x = self.winfo_screenwidth() - _WIDTH - _MARGIN
        y = self.winfo_screenheight() - height - _MARGIN - 40  # над панеллю завдань
        self.geometry(f"{_WIDTH}x{height}+{x}+{y}")
        self._tick()

    def _tick(self) -> None:
        if self._done:
            return
        if self._remaining <= 0:
            self._finish(self._on_timeout)
            return
        self.countdown_label.configure(text=t("toast.apply_in", seconds=max(1, round(self._remaining))))
        self.progress.set(self._remaining / self._seconds)
        self._remaining -= _TICK_MS / 1000
        self._job = self.after(_TICK_MS, self._tick)

    def dismiss(self) -> None:
        """Закрити без жодного колбека (напр., гра вже завершилась)."""
        self._done = True
        self._destroy()

    def _finish(self, callback) -> None:
        if self._done:
            return
        self._done = True
        self._destroy()
        if callback is not None:
            callback()

    def _destroy(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
        try:
            self.destroy()
        except tk.TclError:
            pass
