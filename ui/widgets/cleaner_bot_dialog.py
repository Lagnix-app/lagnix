"""Модальне вікно з роботом-прибиральником (CleanerBotAnimation) поверх Lagnix
(ui/widgets/modal.py) — для тривалих операцій (очищення, видалення програм), щоб
прогрес завжди був помітний, а не загублений на вкладці.
"""

import tkinter as tk

import customtkinter as ctk

from ui.widgets import modal
from ui.widgets.cleaner_bot import CleanerBotAnimation
from core.i18n import t

_AUTO_CLOSE_MS = 5000


class CleanerBotDialog:
    """Робот-прибиральник, статус, прогрес-бар, лічильник звільненого. Поки триває
    операція, вікно не закривається (ні Esc, ні клік по затемненню)."""

    def __init__(self, master, title: str = t("tabs.cleanup"), show_freed_counter: bool = True):
        self._root = master.winfo_toplevel()
        self._show_freed = show_freed_counter
        self._status_text = ""
        self._closable = False
        self._auto_close_id = None
        self._on_cancel = None

        self._modal = modal.Modal(master, title, dismissable=False, scroll=False)
        body = self._modal.body
        self.bot = CleanerBotAnimation(body, height=175)
        self.bot.pack(fill="x", pady=(0, 8))
        self.freed_label = ctk.CTkLabel(body, text=t("dialog.freed_zero"), font=ctk.CTkFont(size=13))
        if self._show_freed:
            self.freed_label.pack(pady=(0, 6))
        self.close_button = self._modal.add_button(t("common.close"), None, "secondary",
                                                   command=self._cancel_or_close, enabled=False)
        self._modal.show()

    # ------------------------------------------------------------- public

    def winfo_exists(self) -> bool:
        return not self._modal.closed

    def start(self, text: str) -> None:
        if self._modal.closed:  # вікно не показано (відкрите інше) або вже закрите
            return
        self._status_text = text
        self.bot.start(text)
        if self._show_freed:
            self.freed_label.configure(text=t("dialog.freed_zero"))

    def set_status(self, text: str) -> None:
        """Оновлює назву поточного пункту, не чіпаючи прогрес."""
        if self._modal.closed:  # вікно не показано (відкрите інше) або вже закрите
            return
        self._status_text = text
        self.bot.update(text)

    def set_progress(self, progress: float, freed_text: str | None = None) -> None:
        if self._modal.closed:  # вікно не показано (відкрите інше) або вже закрите
            return
        self.bot.update(self._status_text, progress)
        if freed_text is not None and self._show_freed:
            self.freed_label.configure(text=t("dialog.freed", freed=freed_text))

    def set_indeterminate(self, active: bool) -> None:
        """Для операцій без відомого прогресу (наприклад, очікування деінсталятора)."""
        if self._modal.closed:  # вікно не показано (відкрите інше) або вже закрите
            return
        if active:
            self.bot.progress.configure(mode="indeterminate")
            self.bot.progress.start()
        else:
            self.bot.progress.stop()
            self.bot.progress.configure(mode="determinate")

    def allow_cancel(self, on_cancel, label: str = t("programs.dont_wait")) -> None:
        """Дозволяє закрити вікно до завершення: on_cancel() сповіщає, що чекати більше не треба."""
        if self._modal.closed:  # вікно не показано (відкрите інше) або вже закрите
            return
        self._on_cancel = on_cancel
        self._set_closable(label)

    def finish(self, text: str, success: bool = True) -> None:
        if self._modal.closed:  # вікно не показано (відкрите інше) або вже закрите
            return
        self._on_cancel = None
        if self.bot.progress.cget("mode") == "indeterminate":
            self.bot.progress.stop()
            self.bot.progress.configure(mode="determinate")
        self._set_closable(t("common.close"))
        self.bot.finish(text, success=success)
        self._auto_close_id = self._root.after(_AUTO_CLOSE_MS, self.destroy)

    # ------------------------------------------------------------- close

    def _set_closable(self, label: str) -> None:
        self._closable = True
        self._modal.dismissable = True
        self.close_button.configure(text=label, state="normal")

    def _cancel_or_close(self) -> None:
        callback, self._on_cancel = self._on_cancel, None
        if callback is not None:
            callback()
        self.destroy()

    def destroy(self) -> None:
        if self._auto_close_id is not None:
            try:
                self._root.after_cancel(self._auto_close_id)
            except tk.TclError:
                pass
            self._auto_close_id = None
        self._modal.close()
