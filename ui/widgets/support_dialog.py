"""Вікно «Підтримати Lagnix»: робот із серцем, подяка й кнопки Ko-fi / itch.io.
Відкривається лише за кліком користувача."""

import tkinter as tk

import customtkinter as ctk

from core import links
from core.i18n import t
from ui import theme
from ui.widgets import robot as robot_view

_WIDTH = 400
_HEIGHT = 440
KOFI_COLOR = "#ff5e5b"
KOFI_COLOR_HOVER = "#ff7a77"


class SupportDialog(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title(t("support.title"))
        self.configure(fg_color=theme.BG_MAIN)
        self.resizable(False, False)

        self._robot = robot_view.RobotView(self, size=140, mood=robot_view.LOVE, bg=theme.BG_MAIN)
        self._robot.pack(pady=(18, 4))
        self._robot.set_running(True)
        ctk.CTkLabel(self, text=t("support.heading"), font=theme.font_header()).pack(pady=(4, 6))
        ctk.CTkLabel(self, text=t("support.text"), font=theme.font_body(), text_color=theme.TEXT_DIM,
                     wraplength=_WIDTH - 60, justify="center").pack(padx=24)

        buttons = theme.plain_frame(self)
        buttons.pack(pady=(18, 6), fill="x", padx=40)
        if links.KOFI_URL:
            ctk.CTkButton(buttons, text="❤  " + t("support.kofi"), height=40, corner_radius=10,
                          fg_color=KOFI_COLOR, hover_color=KOFI_COLOR_HOVER, text_color="#ffffff",
                          font=ctk.CTkFont(size=15, weight="bold"),
                          command=lambda: links.open_link(links.KOFI_URL)).pack(fill="x", pady=(0, 8))
        if links.ITCH_URL:
            ctk.CTkButton(buttons, text=t("support.itch"), height=34, corner_radius=10, fg_color="transparent",
                          border_width=1, border_color=theme.BORDER, hover_color=theme.BG_PANEL_LIGHT,
                          text_color=theme.TEXT_MAIN,
                          command=lambda: links.open_link(links.ITCH_URL)).pack(fill="x")
        ctk.CTkButton(self, text=t("common.close"), width=120, height=30, fg_color="transparent",
                      hover_color=theme.BG_PANEL_LIGHT, text_color=theme.TEXT_DIM,
                      command=self.destroy).pack(pady=(10, 14))

        self._center_over(master)
        self.transient(master)
        self.after(30, self.lift)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Destroy>", self._on_destroy)

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

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            self._robot.set_running(False)
