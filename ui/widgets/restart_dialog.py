"""«Мову буде змінено після перезапуску Lagnix» — мале модальне вікно НОВОЮ мовою
(не поточною) з кнопками «Перезапустити зараз» / «Пізніше»."""

import tkinter as tk

import customtkinter as ctk

from core import i18n
from core.i18n import t
from ui import theme

_WIDTH = 420


class RestartDialog(ctk.CTkToplevel):
    """result: True — «Перезапустити зараз», False — «Пізніше» (також Esc і закриття вікна)."""

    def __init__(self, master, language: str):
        super().__init__(master)
        self.result = False
        family = i18n.ui_font_family(language)  # шрифт НОВОЇ мови (CJK без «квадратиків»)
        with i18n.using_language(language):
            title, text = t("restart.title"), t("restart.text")
            now_text, later_text = t("restart.now"), t("restart.later")
        self.title("Lagnix")
        self.resizable(False, False)
        self.configure(fg_color=theme.BG_PANEL)
        self.transient(master.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self._later)

        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(family=family, size=17, weight="bold"), anchor="w").pack(
            fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(self, text=text, font=ctk.CTkFont(family=family, size=13), text_color=theme.TEXT_DIM,
                     anchor="w", justify="left", wraplength=_WIDTH - 40).pack(fill="x", padx=20)
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(18, 18))
        ctk.CTkButton(buttons, text=now_text, width=10, height=32, corner_radius=8, fg_color=theme.ACCENT_BLUE_DIM,
                      font=ctk.CTkFont(family=family, size=13), command=self._now).pack(side="right")
        ctk.CTkButton(buttons, text=later_text, width=100, height=32, corner_radius=8, fg_color="transparent",
                      border_width=1, border_color=theme.BORDER, hover_color=theme.BG_PANEL_LIGHT,
                      text_color=theme.TEXT_MAIN, font=ctk.CTkFont(family=family, size=13),
                      command=self._later).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _e: self._later())

        self.update_idletasks()
        root = master.winfo_toplevel()
        x = root.winfo_rootx() + (root.winfo_width() - max(self.winfo_reqwidth(), _WIDTH)) // 2
        y = root.winfo_rooty() + (root.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"{max(self.winfo_reqwidth(), _WIDTH)}x{self.winfo_reqheight()}+{max(x, 0)}+{max(y, 0)}")
        self.after(30, self._grab)

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def _now(self) -> None:
        self.result = True
        self.destroy()

    def _later(self) -> None:
        self.result = False
        self.destroy()


def ask_restart(master, language: str) -> bool:
    dialog = RestartDialog(master, language)
    dialog.wait_window()
    return dialog.result
