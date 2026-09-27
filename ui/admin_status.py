"""Індикатор прав адміністратора та кнопка перезапуску через UAC.

Використовується у бічному меню (AdminStatusPanel) і в місцях вкладок,
де через брак прав щось недоступне (ElevateButton).
"""

from tkinter import messagebox

import customtkinter as ctk

from core import admin as admin_core


def elevate_and_restart(widget) -> None:
    """Просить UAC підняти права й закриває поточний екземпляр PulseFPS."""
    if admin_core.relaunch_as_admin():
        widget.winfo_toplevel().destroy()
    else:
        messagebox.showerror(
            "Помилка",
            "Не вдалося перезапустити програму з правами адміністратора.",
            parent=widget,
        )


class ElevateButton(ctk.CTkButton):
    """Кнопка «Перезапустити як адміністратор» для повторного використання у вкладках."""

    def __init__(self, master, **kwargs):
        kwargs.setdefault("text", "Перезапустити як адміністратор")
        kwargs.setdefault("height", 26)
        kwargs.setdefault("font", ctk.CTkFont(size=11))
        super().__init__(master, **kwargs)
        self.configure(command=lambda: elevate_and_restart(self))


class AdminStatusPanel(ctk.CTkFrame):
    """Статус прав адміністратора для низу бічного меню."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.grid_columnconfigure(0, weight=1)

        admin = admin_core.is_admin()
        text = "● Адміністратор" if admin else "● Обмежений режим"
        color = "#2fa572" if admin else "#e0a52f"

        ctk.CTkLabel(
            self, text=text, text_color=color, font=ctk.CTkFont(size=12, weight="bold"), anchor="w"
        ).grid(row=0, column=0, sticky="ew")

        if not admin:
            ElevateButton(self, width=1).grid(row=1, column=0, pady=(6, 0), sticky="ew")
