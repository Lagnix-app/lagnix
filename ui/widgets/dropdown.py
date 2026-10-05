"""Спільний випадний список (замість CTkOptionMenu і власного LanguagePicker).

Поле «текст ▾» і спливний список у власному Toplevel без рамки:
  * клік будь-де на полі (текст чи стрілка) лише перемикає: закритий → відкрити,
    відкритий → закрити (усі частини поля ведуть в один обробник; повторний
    виклик протягом 200 мс ігнорується — щоб одна дія не спрацювала двічі);
  * список має ширину поля, відкривається вниз, а якщо знизу мало місця — вгору;
  * закривається при кліку поза ним, Esc, втраті фокуса, прокручуванні сторінки
    (коліщатко поза списком), переміщенні/зміні розміру вікна, перемиканні вкладки
    (Dropdown.close_all()) і знищенні поля;
  * коліщатко над списком прокручує сам список, якщо він довгий.
"""

from __future__ import annotations

import time
import tkinter as tk
from typing import Callable

import customtkinter as ctk

from ui import theme

_ROW_H = 30
_MAX_LIST_H = 320
_DEBOUNCE_S = 0.2

_open: list["Dropdown"] = []


class Dropdown(ctk.CTkFrame):
    def __init__(self, master, values: list[str], command: Callable[[str], None] | None = None,
                 variable: tk.StringVar | None = None, width: int = 220, height: int = 30,
                 corner_radius: int = 8, font=None, item_font: Callable[[str], ctk.CTkFont] | None = None,
                 value: str | None = None):
        super().__init__(master, width=width, height=height, corner_radius=corner_radius,
                         fg_color=theme.BG_PANEL_LIGHT)
        self.values = list(values)
        self._command = command
        self._variable = variable
        self._item_font = item_font
        self._font = font or theme.font_body()
        self._row_h = max(_ROW_H, height - 4)
        self._value = value if value is not None else (variable.get() if variable is not None else "")
        self._popup: tk.Toplevel | None = None
        self._last_toggle = 0.0
        self._root_binding: tuple[tk.Misc, str] | None = None
        self.grid_propagate(False)
        self.pack_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._text = ctk.CTkLabel(self, text=self._value, font=self._font, text_color=theme.TEXT_MAIN,
                                  anchor="w", fg_color="transparent")
        self._text.grid(row=0, column=0, sticky="nsew", padx=(12, 0))
        self._arrow = ctk.CTkLabel(self, text="▾", font=ctk.CTkFont(size=13), width=14,
                                   text_color=theme.TEXT_DIM, fg_color="transparent")
        self._arrow.grid(row=0, column=1, padx=(4, 10))
        self._apply_item_font(self._value)

        for widget in (self, self._text, self._arrow):
            widget.bind("<Button-1>", self._on_field_click, add="+")
            widget.bind("<Enter>", lambda _e: self.configure(fg_color=theme.BORDER), add="+")
            widget.bind("<Leave>", self._on_leave, add="+")
        # CTkLabel малює через внутрішні tk-віджети — прив'язки теж на них
        for label in (self._text, self._arrow):
            for inner in (getattr(label, "_canvas", None), getattr(label, "_label", None)):
                if inner is not None:
                    inner.bind("<Button-1>", self._on_field_click, add="+")
                    inner.bind("<Enter>", lambda _e: self.configure(fg_color=theme.BORDER), add="+")
                    inner.bind("<Leave>", self._on_leave, add="+")
        self.bind("<Destroy>", lambda e: self.close() if e.widget is self else None, add="+")
        if variable is not None:
            variable.trace_add("write", self._on_variable)

    # ------------------------------------------------------------ значення

    def get(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        self._value = value
        self._text.configure(text=value)
        self._apply_item_font(value)
        if self._variable is not None and self._variable.get() != value:
            self._variable.set(value)

    def configure_values(self, values: list[str]) -> None:
        self.values = list(values)

    def _on_variable(self, *_args) -> None:
        try:
            value = self._variable.get()
        except tk.TclError:
            return
        if value != self._value:
            self._value = value
            try:
                self._text.configure(text=value)
                self._apply_item_font(value)
            except tk.TclError:
                pass

    def _apply_item_font(self, value: str) -> None:
        self._text.configure(font=self._item_font(value) if self._item_font else self._font)

    def _on_leave(self, _event) -> None:
        if self._popup is None:
            self.configure(fg_color=theme.BG_PANEL_LIGHT)

    # --------------------------------------------------------------- список

    @staticmethod
    def close_all() -> None:
        for dropdown in list(_open):
            dropdown.close()

    def _on_field_click(self, _event=None) -> str:
        now = time.monotonic()
        if now - self._last_toggle >= _DEBOUNCE_S:
            self._last_toggle = now
            self.toggle()
        return "break"

    def toggle(self) -> None:
        if self._popup is not None:
            self.close()
        else:
            self.open()

    def open(self) -> None:
        if self._popup is not None:
            return
        Dropdown.close_all()
        popup = self._popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.configure(bg=theme.BORDER)
        popup.attributes("-topmost", True)
        self.update_idletasks()
        width = self.winfo_width()

        content_h = len(self.values) * (self._row_h + 2) + 6
        list_h = min(content_h, _MAX_LIST_H)
        if content_h > _MAX_LIST_H:
            body = ctk.CTkScrollableFrame(popup, fg_color=theme.BG_PANEL, corner_radius=0, width=width - 2,
                                          height=list_h, scrollbar_button_color=theme.BORDER)
            holder = body
        else:
            body = ctk.CTkFrame(popup, fg_color=theme.BG_PANEL, corner_radius=0)
            holder = body
        body.pack(fill="both", expand=True, padx=1, pady=1)
        for item in self.values:
            selected = item == self._value
            ctk.CTkButton(
                holder, text=item, anchor="w", width=width - 16, height=self._row_h, corner_radius=6,
                font=self._item_font(item) if self._item_font else self._font,
                fg_color=theme.BG_PANEL_LIGHT if selected else "transparent",
                hover_color=theme.BORDER, text_color=theme.ACCENT_GREEN if selected else theme.TEXT_MAIN,
                command=lambda v=item: self._choose(v),
            ).pack(fill="x", padx=4, pady=1)

        popup.update_idletasks()
        height = popup.winfo_reqheight()
        x = self.winfo_rootx()
        below = self.winfo_rooty() + self.winfo_height() + 2
        if below + height > self.winfo_screenheight() - 8:  # знизу мало місця — вгору
            y = max(8, self.winfo_rooty() - height - 2)
        else:
            y = below
        popup.geometry(f"{width}x{height}+{x}+{y}")
        popup.deiconify()
        popup.lift()
        _open.append(self)
        self.configure(fg_color=theme.BORDER)

        popup.bind("<Escape>", lambda _e: self.close())
        popup.bind("<Button-1>", self._on_popup_click, add="+")
        popup.bind("<MouseWheel>", self._on_popup_wheel, add="+")
        popup.bind("<FocusOut>", lambda _e: self.after(50, self._close_if_unfocused), add="+")
        top = self.winfo_toplevel()
        self._root_binding = (top, top.bind("<Configure>", self._on_root_configure, add="+"))
        popup.focus_force()
        try:
            popup.grab_set()
        except tk.TclError:
            pass

    def close(self) -> None:
        popup, self._popup = self._popup, None
        if self in _open:
            _open.remove(self)
        binding, self._root_binding = self._root_binding, None
        if binding is not None:
            top, funcid = binding
            try:
                top.unbind("<Configure>", funcid)
            except tk.TclError:
                pass
        if popup is not None:
            try:
                popup.grab_release()
                popup.destroy()
            except tk.TclError:
                pass
            try:
                self.configure(fg_color=theme.BG_PANEL_LIGHT)
            except tk.TclError:
                pass

    def _inside_popup(self, event) -> bool:
        popup = self._popup
        if popup is None:
            return False
        return (popup.winfo_rootx() <= event.x_root < popup.winfo_rootx() + popup.winfo_width()
                and popup.winfo_rooty() <= event.y_root < popup.winfo_rooty() + popup.winfo_height())

    def _on_popup_click(self, event) -> None:
        if not self._inside_popup(event):
            # клік по самому полю: закриває список; повторний клік не відкриє його знову
            self._last_toggle = time.monotonic()
            self.close()

    def _on_popup_wheel(self, event) -> str | None:
        """Над списком — прокручує його (довгий список), поза ним — закриває."""
        if not self._inside_popup(event):
            self.close()
            return None
        # CTkScrollableFrame сам обробляє колесо; для короткого списку сторінка не їде
        return "break" if len(self.values) * (self._row_h + 2) + 6 <= _MAX_LIST_H else None

    def _on_root_configure(self, event) -> None:
        if event.widget is self.winfo_toplevel():
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

    def _choose(self, value: str) -> None:
        self.close()
        if value == self._value:
            return
        self.set(value)
        if self._command is not None:
            self._command(value)
