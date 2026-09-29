"""Модальне вікно з роботом-прибиральником (CleanerBotAnimation) поверх
PulseFPS — використовується для тривалих операцій (очищення, видалення
програм), щоб прогрес завжди був помітний, а не загублений на вкладці.
"""

import tkinter as tk

import customtkinter as ctk

from ui.widgets.cleaner_bot import CleanerBotAnimation

_WIDTH = 420
_HEIGHT = 380
_AUTO_CLOSE_MS = 5000


class CleanerBotDialog(ctk.CTkToplevel):
    """Модальне вікно: робот-прибиральник, статус, прогрес-бар, лічильник звільненого."""

    def __init__(self, master, title: str = "Очищення", show_freed_counter: bool = True):
        super().__init__(master)
        self.title(title)
        self.geometry(f"{_WIDTH}x{_HEIGHT}")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close_attempt)

        self._show_freed = show_freed_counter
        self._status_text = ""
        self._closable = False
        self._auto_close_id = None
        self._on_cancel = None

        self.bot = CleanerBotAnimation(self, height=175)
        self.bot.pack(fill="x", padx=16, pady=(16, 8))

        self.freed_label = ctk.CTkLabel(self, text="Звільнено: 0 Б", font=ctk.CTkFont(size=13))
        if self._show_freed:
            self.freed_label.pack(pady=(0, 10))

        self.close_button = ctk.CTkButton(
            self, text="Закрити", width=140, state="disabled", command=self._cancel_or_close
        )
        self.close_button.pack(pady=(0, 16))

        self._center_over(master)
        self.transient(master)
        self.lift()
        self.after(30, self._grab)

    # ------------------------------------------------------------- setup

    def _grab(self) -> None:
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_force()

    def _center_over(self, master) -> None:
        self.update_idletasks()
        try:
            mx, my = master.winfo_rootx(), master.winfo_rooty()
            mw, mh = master.winfo_width(), master.winfo_height()
        except tk.TclError:
            mx = my = mw = mh = 0
        x = max(mx + (mw - _WIDTH) // 2, 0)
        y = max(my + (mh - _HEIGHT) // 2, 0)
        self.geometry(f"{_WIDTH}x{_HEIGHT}+{x}+{y}")

    def _on_close_attempt(self) -> None:
        if self._closable:
            self._cancel_or_close()
        # поки триває операція — закрити хрестиком не можна

    def _cancel_or_close(self) -> None:
        callback, self._on_cancel = self._on_cancel, None
        if callback is not None:
            callback()
        self.destroy()

    # ------------------------------------------------------------ public

    def start(self, text: str) -> None:
        self._status_text = text
        self.bot.start(text)
        if self._show_freed:
            self.freed_label.configure(text="Звільнено: 0 Б")

    def set_status(self, text: str) -> None:
        """Оновлює назву поточного пункту, не чіпаючи прогрес."""
        self._status_text = text
        self.bot.update(text)

    def set_progress(self, progress: float, freed_text: str | None = None) -> None:
        self.bot.update(self._status_text, progress)
        if freed_text is not None and self._show_freed:
            self.freed_label.configure(text=f"Звільнено: {freed_text}")

    def set_indeterminate(self, active: bool) -> None:
        """Для операцій без відомого прогресу (наприклад, очікування деінсталятора)."""
        if active:
            self.bot.progress.configure(mode="indeterminate")
            self.bot.progress.start()
        else:
            self.bot.progress.stop()
            self.bot.progress.configure(mode="determinate")

    def allow_cancel(self, on_cancel, label: str = "Не чекати") -> None:
        """Дозволяє закрити вікно до завершення: on_cancel() сповіщає, що чекати більше не треба."""
        self._on_cancel = on_cancel
        self._closable = True
        self.close_button.configure(text=label, state="normal")

    def finish(self, text: str, success: bool = True) -> None:
        self._on_cancel = None
        self.close_button.configure(text="Закрити")
        if self.bot.progress.cget("mode") == "indeterminate":
            self.bot.progress.stop()
            self.bot.progress.configure(mode="determinate")

        self._closable = True
        self.close_button.configure(state="normal")
        self.bot.finish(text, success=success)
        self._auto_close_id = self.after(_AUTO_CLOSE_MS, self._auto_close)

    # ------------------------------------------------------------- close

    def _auto_close(self) -> None:
        self._auto_close_id = None
        self.destroy()

    def destroy(self) -> None:
        if self._auto_close_id is not None:
            try:
                self.after_cancel(self._auto_close_id)
            except tk.TclError:
                pass
            self._auto_close_id = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        super().destroy()
