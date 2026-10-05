"""Вікно «Підтримати Lagnix»: робот із серцем, подяка й кнопка Ko-fi. Модальне вікно
всередині головного (ui/widgets/modal.py); відкривається лише за кліком користувача."""

import customtkinter as ctk

from core import links
from core.i18n import t
from ui import theme
from ui.widgets import modal
from ui.widgets import robot as robot_view

KOFI_COLOR = "#ff5e5b"
KOFI_COLOR_HOVER = "#ff7a77"
ROBOT_SIZE = 140


def show_support(master) -> None:
    window = modal.Modal(master, t("support.heading"), t("support.text"))
    body = window.body
    robot = robot_view.RobotView(body, size=ROBOT_SIZE, mood=robot_view.LOVE, bg=theme.BG_PANEL)
    robot.pack(pady=(8, 4))
    robot.set_running(True)
    window.on_close.append(lambda _value: robot.set_running(False))
    if links.KOFI_URL:
        ctk.CTkButton(body, text="❤  " + t("support.kofi"), height=40, corner_radius=10,
                      fg_color=KOFI_COLOR, hover_color=KOFI_COLOR_HOVER, text_color="#ffffff",
                      font=ctk.CTkFont(size=15, weight="bold"),
                      command=lambda: links.open_link(links.KOFI_URL)).pack(fill="x", padx=60, pady=(14, 4))
    window.add_button(t("common.close"), None, "ghost")
    window.show()
