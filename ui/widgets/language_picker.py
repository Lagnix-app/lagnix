"""Випадний список мов із прапорцями: кнопка «[прапорець] Назва ▾», по кліку —
спливний список усіх мов (прапорець + назва мови рідною мовою, кожна — своїм
шрифтом, щоб ієрогліфи не ставали квадратиками).

CTkOptionMenu не вміє показувати картинки в пунктах, тому список — власний
Toplevel без рамки: grab_set() перехоплює кліки, клік поза списком, Esc чи
втрата фокуса його закривають.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk
from PIL import Image

from core import i18n
from ui import theme
from ui.widgets import flags

_ROW_H = 32
_WIDTH = 240

_images: dict[str, ctk.CTkImage] = {}


def flag_image(code: str) -> ctk.CTkImage:
    """CTkImage прапорця 20x14 (джерело — @2x, тож на 125–200% лишається чітким)."""
    image = _images.get(code)
    if image is None:
        flags.ensure_flags()
        source = Image.open(flags.flag_path(code, 2))
        image = _images[code] = ctk.CTkImage(light_image=source, dark_image=source, size=flags.SIZE)
    return image


def _font(code: str, size: int = 13) -> ctk.CTkFont:
    return ctk.CTkFont(family=i18n.ui_font_family(code), size=size)


class LanguagePicker(ctk.CTkFrame):
    def __init__(self, master, current: str, on_select, width: int = _WIDTH):
        super().__init__(master, fg_color="transparent")
        self._current = current
        self._on_select = on_select
        self._width = width
        self._popup: tk.Toplevel | None = None
        self.button = ctk.CTkButton(
            self, text=i18n.language_name(current), image=flag_image(current), compound="left", anchor="w",
            width=width, height=_ROW_H, corner_radius=8, font=_font(current),
            fg_color=theme.BG_PANEL_LIGHT, hover_color=theme.BORDER, text_color=theme.TEXT_MAIN,
            command=self.toggle,
        )
        self.button.pack(anchor="w")
        arrow = ctk.CTkLabel(self.button, text="▾", text_color=theme.TEXT_DIM, fg_color="transparent",
                             font=ctk.CTkFont(size=13), width=14)
        arrow.place(relx=1.0, x=-12, rely=0.5, anchor="e")
        arrow.bind("<Button-1>", lambda _e: self.toggle(), add="+")
        self.bind("<Destroy>", lambda e: self.close() if e.widget is self else None, add="+")

    # ---------------------------------------------------------------- список

    def toggle(self) -> None:
        if self._popup is not None:
            self.close()
        else:
            self.open()

    def open(self) -> None:
        popup = self._popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.configure(bg=theme.BORDER)
        popup.attributes("-topmost", True)
        body = ctk.CTkFrame(popup, fg_color=theme.BG_PANEL, corner_radius=0)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        for code, name in i18n.LANGUAGES:
            selected = code == self._current
            ctk.CTkButton(
                body, text=name, image=flag_image(code), compound="left", anchor="w",
                width=self._width - 2, height=_ROW_H, corner_radius=6, font=_font(code),
                fg_color=theme.BG_PANEL_LIGHT if selected else "transparent",
                hover_color=theme.BORDER, text_color=theme.ACCENT_GREEN if selected else theme.TEXT_MAIN,
                command=lambda c=code: self._choose(c),
            ).pack(fill="x", padx=4, pady=1)

        popup.update_idletasks()
        x = self.button.winfo_rootx()
        y = self.button.winfo_rooty() + self.button.winfo_height() + 4
        height = popup.winfo_reqheight()
        screen_bottom = self.winfo_screenheight() - 8
        if y + height > screen_bottom:  # не влазить донизу — розкриваємо вгору
            y = max(8, self.button.winfo_rooty() - height - 4)
        popup.geometry(f"+{x}+{y}")
        popup.deiconify()
        popup.lift()
        popup.bind("<Escape>", lambda _e: self.close())
        popup.bind("<Button-1>", self._on_popup_click, add="+")
        popup.bind("<FocusOut>", lambda _e: self.after(50, self._close_if_unfocused), add="+")
        popup.focus_force()
        try:
            popup.grab_set()
        except tk.TclError:
            pass

    def close(self) -> None:
        popup, self._popup = self._popup, None
        if popup is not None:
            try:
                popup.grab_release()
                popup.destroy()
            except tk.TclError:
                pass

    def _on_popup_click(self, event) -> None:
        popup = self._popup
        if popup is None:
            return
        inside = (popup.winfo_rootx() <= event.x_root < popup.winfo_rootx() + popup.winfo_width()
                  and popup.winfo_rooty() <= event.y_root < popup.winfo_rooty() + popup.winfo_height())
        if not inside:
            self.close()

    def _close_if_unfocused(self) -> None:
        popup = self._popup
        if popup is None:
            return
        try:
            focus = popup.focus_get()
        except (tk.TclError, KeyError):
            focus = None
        if focus is None or not str(focus).startswith(str(popup)):
            self.close()

    def _choose(self, code: str) -> None:
        self.close()
        if code == self._current:
            return
        self._current = code
        self.button.configure(text=i18n.language_name(code), image=flag_image(code), font=_font(code))
        self._on_select(code)
