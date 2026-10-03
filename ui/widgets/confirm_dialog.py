"""Модальне підтвердження в стилі PulseFPS. На відміну від messagebox дозволяє
виділити дію кольором: danger=True — червона кнопка (скидання, очищення), і
саме у вікні підтвердження, а не на кнопці, що його відкриває."""

import tkinter as tk

import customtkinter as ctk

from ui import theme
from core.i18n import t

_WIDTH = 440


class ConfirmDialog(ctk.CTkToplevel):
    def __init__(self, master, title: str, message: str, confirm_text: str, danger: bool = False,
                 cancel_text: str = t("common.cancel")):
        super().__init__(master)
        self.result = False
        self.title(title)
        self.resizable(False, False)
        self.configure(fg_color=theme.BG_PANEL)
        self.transient(master.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        ctk.CTkLabel(self, text=title, font=theme.font_header(), anchor="w").pack(
            fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(self, text=message, font=theme.font_body(), text_color=theme.TEXT_DIM, anchor="w",
                     justify="left", wraplength=_WIDTH - 40).pack(fill="x", padx=20)
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(18, 18))
        confirm_style = ({"fg_color": "#a8283f", "hover_color": theme.ERROR, "text_color": "#ffffff"} if danger
                         else {"fg_color": theme.ACCENT_BLUE_DIM})
        self.confirm_button = ctk.CTkButton(buttons, text=confirm_text, width=10, height=32, corner_radius=8,
                                            command=self._confirm, **confirm_style)
        self.confirm_button.pack(side="right")
        ctk.CTkButton(buttons, text=cancel_text, width=100, height=32, corner_radius=8, fg_color="transparent",
                      border_width=1, border_color=theme.BORDER, hover_color=theme.BG_PANEL_LIGHT,
                      text_color=theme.TEXT_MAIN, command=self._cancel).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _e: self._cancel())

        self.update_idletasks()
        root = master.winfo_toplevel()
        x = root.winfo_rootx() + (root.winfo_width() - self.winfo_reqwidth()) // 2
        y = root.winfo_rooty() + (root.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.after(30, self._grab)

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def _confirm(self) -> None:
        self.result = True
        self.destroy()

    def _cancel(self) -> None:
        self.result = False
        self.destroy()


def ask(master, title: str, message: str, confirm_text: str, danger: bool = False) -> bool:
    """Показує діалог і чекає відповіді (як messagebox.askyesno)."""
    dialog = ConfirmDialog(master, title, message, confirm_text, danger)
    master.wait_window(dialog)
    return dialog.result
