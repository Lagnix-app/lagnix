"""Віртуалізований список карток програм для вкладки «Програми».

Замість сотень віджетів тримаємо невеликий пул рядків (стільки, скільки
влазить у видиму область + запас) і при прокрутці лише перепризначаємо їм
програми та зсуваємо через place(). Тому 70 чи 700 карток прокручуються однаково
плавно. Наведення визначається за координатами курсора (а не Enter/Leave рядка),
щоб підсвічування не мерехтіло при переході між дочірніми віджетами картки.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from datetime import date

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont, ImageTk

from core.app_icons import IconLoader
from core.cleanup import format_size
from ui import theme
from ui.widgets import aa

ROW_H = 68  # dp: крок рядків у списку
CARD_H = 62  # dp: висота самої картки (решта — проміжок)
ICON_DP = 32

# ширини колонок (dp) — за ними ж рахується місце під назву
COL_CHECK, COL_ICON, COL_BADGE, COL_SIZE, COL_RIGHT = 34, 46, 66, 112, 206
_ROW_EXTRA_DP = 24  # відступи по краях картки

CATEGORIES = (
    ("game", "Ігри", theme.ACCENT_BLUE),
    ("app", "Програми", theme.ACCENT_GREEN),
    ("system", "Системні", "#c77dff"),
    ("other", "Інше", "#5b6680"),
)
CATEGORY_COLORS = {key: color for key, _label, color in CATEGORIES}
STORE_COLORS = {"Steam": "#66c0f4", "Epic": "#dfe6f2", "Riot": theme.ERROR, "Rockstar": theme.WARNING}

_PLACEHOLDER_COLORS = (theme.ACCENT_BLUE, theme.ACCENT_GREEN, "#c77dff", theme.WARNING, "#ff8a5c", "#5cd6c0")
_FONT_FAMILY = "Segoe UI"
_DELETE_HOVER = "#e04a68"


# ---------------------------------------------------------------- форматування

def plural(n: int, forms: tuple[str, str, str]) -> str:
    """Українська множина: (1 програма, 2 програми, 5 програм)."""
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return forms[0]
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return forms[1]
    return forms[2]


def relative_date(d: date | None) -> str:
    if d is None:
        return "—"
    days = (date.today() - d).days
    if days <= 0:
        return "сьогодні"
    if days == 1:
        return "вчора"
    if days < 30:
        return f"{days} {plural(days, ('день', 'дні', 'днів'))} тому"
    if days < 365:
        months = days // 30
        return f"{months} {plural(months, ('місяць', 'місяці', 'місяців'))} тому"
    years = days // 365
    return f"{years} {plural(years, ('рік', 'роки', 'років'))} тому"


def size_text(program: dict) -> str:
    source = program.get("size_source")
    if source == "computing":
        return "рахую…"
    if not program["size_bytes"]:
        return "—"
    text = format_size(program["size_bytes"])
    return f"~{text}" if source in ("folder", "steam") else text


# ------------------------------------------------------------------ плейсхолдер

def _mix(fg: tuple, bg: tuple, t: float) -> tuple:
    return tuple(round(bg[i] + (fg[i] - bg[i]) * t) for i in range(3))


def _placeholder_image(name: str, size_px: int) -> Image.Image:
    """Кругла заглушка з першою літерою назви (колір — за хешем назви)."""
    color = aa.rgb(_PLACEHOLDER_COLORS[sum(map(ord, name)) % len(_PLACEHOLDER_COLORS)])
    big = size_px * aa.SS
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse((0, 0, big - 1, big - 1), fill=_mix(color, aa.rgb(theme.BG_PANEL), 0.22) + (255,))
    letter = next((ch for ch in name if ch.isalnum()), "?").upper()
    try:
        font = ImageFont.truetype("segoeuib.ttf", round(big * 0.5))
    except OSError:
        font = ImageFont.load_default()
    d.text((big / 2, big / 2), letter, fill=color + (255,), font=font, anchor="mm")
    return aa.downscale(layer, (size_px, size_px))


# ---------------------------------------------------------------------- підказка

class _Tip:
    def __init__(self, owner: tk.Widget):
        self._owner = owner
        self._win: tk.Toplevel | None = None

    def show(self, text: str, x_root: int, y_root: int) -> None:
        self.hide()
        win = tk.Toplevel(self._owner)
        win.wm_overrideredirect(True)
        win.wm_geometry(f"+{x_root + 12}+{y_root + 18}")
        tk.Label(
            win, text=text, background="#1a1a1a", foreground="#dce4ee", font=("Segoe UI", 10),
            padx=8, pady=4, relief="solid", borderwidth=1, wraplength=380, justify="left",
        ).pack()
        self._win = win

    def hide(self) -> None:
        if self._win is not None:
            self._win.destroy()
            self._win = None


# -------------------------------------------------------------------------- рядок

class ProgramRow(theme.PlainFrame):
    """Картка однієї програми. Створюється один раз і перепризначається (bind_program)."""

    def __init__(self, master, owner: "VirtualList"):
        super().__init__(
            master, corner_radius=10, fg_color=theme.BG_PANEL, border_width=1,
            border_color=theme.BORDER, height=CARD_H,
        )
        self.grid_propagate(False)
        self._owner = owner
        self.index: int | None = None
        self.program: dict | None = None
        self._hovered = False
        self._selected = False
        self._icon_photo = None
        self._full_name = ""
        self._name_truncated = False

        S = self._get_widget_scaling()
        for col, width in enumerate((COL_CHECK, COL_ICON, 0, COL_BADGE, COL_SIZE, COL_RIGHT)):
            self.grid_columnconfigure(col, weight=1 if col == 2 else 0, minsize=round(width * S))
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        f = owner.fonts
        self.check = ctk.CTkCheckBox(
            self, text="", width=24, checkbox_width=20, checkbox_height=20, corner_radius=6, border_width=2,
            fg_color=theme.ACCENT_GREEN, hover_color=theme.ACCENT_GREEN_DIM, border_color=theme.TEXT_DIM,
            checkmark_color=theme.BG_MAIN, command=self._on_check,
        )
        self.check.grid(row=0, column=0, rowspan=2, padx=(12, 0))

        self.icon_label = tk.Label(self, bd=0, highlightthickness=0, bg=theme.BG_PANEL)
        self.icon_label.grid(row=0, column=1, rowspan=2)

        self.name_label = ctk.CTkLabel(self, text="", font=f["name"], text_color=theme.TEXT_MAIN, anchor="w")
        self.name_label.grid(row=0, column=2, sticky="sw", padx=(0, 8), pady=(6, 0))
        self.publisher_label = ctk.CTkLabel(self, text="", font=f["small"], text_color=theme.TEXT_DIM, anchor="w")
        self.publisher_label.grid(row=1, column=2, sticky="nw", padx=(0, 8), pady=(0, 6))

        self.badge = ctk.CTkLabel(
            self, text="", font=f["badge"], fg_color=theme.BORDER, corner_radius=6, width=50, height=20,
        )
        self.badge.grid(row=0, column=3, rowspan=2)

        self.size_label = ctk.CTkLabel(self, text="", font=f["size"], text_color=theme.TEXT_MAIN, anchor="e")
        self.size_label.grid(row=0, column=4, sticky="sew", padx=(0, 14), pady=(6, 0))
        self.bar = ctk.CTkProgressBar(
            self, width=COL_SIZE - 14, height=4, corner_radius=2, fg_color=theme.BORDER,
            progress_color=theme.ACCENT_GREEN, border_width=0,
        )
        self.bar.grid(row=1, column=4, sticky="ne", padx=(0, 14), pady=(5, 0))

        self.date_label = ctk.CTkLabel(self, text="", font=f["small"], text_color=theme.TEXT_DIM, anchor="e")
        self.date_label.grid(row=0, column=5, rowspan=2, sticky="e", padx=(0, 16))

        self.open_button = ctk.CTkButton(
            self, text="Відкрити папку", width=112, height=28, corner_radius=8, font=f["small"],
            fg_color=theme.BORDER, hover_color="#2d3953", text_color=theme.TEXT_MAIN,
            command=lambda: owner.on_open(self.program),
        )
        self.delete_button = ctk.CTkButton(
            self, text="Видалити", width=80, height=28, corner_radius=8, font=f["small"],
            fg_color=theme.ERROR, hover_color=_DELETE_HOVER, text_color="#ffffff",
            command=lambda: owner.on_uninstall(self.program),
        )

        self.date_label.bind("<Enter>", self._on_date_enter, add="+")
        self.date_label.bind("<Leave>", lambda _e: owner.tip.hide(), add="+")
        self.name_label.bind("<Enter>", self._on_name_enter, add="+")
        self.name_label.bind("<Leave>", lambda _e: owner.tip.hide(), add="+")
        self._bind_pointer(self)

    def _bind_pointer(self, widget) -> None:
        """Motion/Leave/Double-click з усіх вкладених віджетів — у список."""
        widget.bind("<Motion>", self._owner.schedule_hover, add="+")
        widget.bind("<Leave>", self._owner.schedule_hover, add="+")
        widget.bind("<MouseWheel>", self._owner.on_wheel, add="+")
        if not isinstance(widget, (ctk.CTkButton, ctk.CTkCheckBox)):
            widget.bind("<Double-Button-1>", self._on_double_click, add="+")
        for child in widget.winfo_children():
            self._bind_pointer(child)

    # ---------------------------------------------------------------- bind

    def bind_program(self, index: int, program: dict, *, name_px: int, selected: bool, hovered: bool,
                     max_size: int, icon) -> None:
        self.index = index
        self.program = program
        self._full_name = program["name"]
        shown = self._owner.truncate(self._full_name, name_px, "name")
        self._name_truncated = shown != self._full_name
        self.name_label.configure(text=shown)
        self.publisher_label.configure(text=self._owner.truncate(program.get("publisher") or "—", name_px, "small"))

        store = program.get("store")
        if store:
            self.badge.configure(text=store, text_color=STORE_COLORS.get(store, theme.TEXT_DIM))
            self.badge.grid()
        else:
            self.badge.grid_remove()

        self.refresh_size(program, max_size)
        self.date_label.configure(text=relative_date(program.get("install_date")))
        self.set_icon(icon)
        self.open_button.configure(state="normal" if program.get("install_folder") else "disabled")

        self._selected = selected
        if selected:
            self.check.select()
        else:
            self.check.deselect()
        self._hovered = None  # примусово перемалювати стан
        self.set_hovered(hovered)

    def refresh_size(self, program: dict, max_size: int) -> None:
        self.size_label.configure(text=size_text(program))
        size = program["size_bytes"]
        self.bar.configure(progress_color=CATEGORY_COLORS.get(program.get("category"), theme.ACCENT_GREEN))
        self.bar.set(max(size / max_size, 0.02) if size and max_size else 0)

    def set_icon(self, photo) -> None:
        self._icon_photo = photo
        self.icon_label.configure(image=photo)

    # --------------------------------------------------------------- стан

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_state()

    def set_hovered(self, hovered: bool) -> None:
        if hovered == self._hovered:
            return
        self._hovered = hovered
        self._apply_state()
        if hovered:
            self.date_label.grid_remove()
            self.delete_button.place(relx=1.0, x=-14, rely=0.5, anchor="e")
            if self.program and self.program.get("install_folder"):
                self.open_button.place(relx=1.0, x=-100, rely=0.5, anchor="e")
        else:
            self.open_button.place_forget()
            self.delete_button.place_forget()
            self.date_label.grid()

    def _apply_state(self) -> None:
        fill = theme.BG_PANEL_LIGHT if self._hovered else theme.BG_PANEL
        if self._hovered:
            border = theme.ACCENT_BLUE
        elif self._selected:
            border = theme.ACCENT_GREEN_DIM
        else:
            border = theme.BORDER
        self.configure(fg_color=fill, border_color=border)
        self.icon_label.configure(bg=fill)

    # ------------------------------------------------------------ обробники

    def _on_check(self) -> None:
        if self.program is None:
            return
        self._selected = bool(self.check.get())
        self._apply_state()
        self._owner.on_toggle(self.program, self._selected)

    def _on_double_click(self, _event) -> None:
        if self.program is not None:
            self._owner.on_open(self.program)

    def _on_date_enter(self, event) -> None:
        d = self.program.get("install_date") if self.program else None
        if d is not None:
            self._owner.tip.show(f"Встановлено: {d.strftime('%d.%m.%Y')}", event.x_root, event.y_root)

    def _on_name_enter(self, event) -> None:
        if self._name_truncated and self.program:
            version = self.program.get("version")
            text = f"{self._full_name}\nВерсія: {version}" if version else self._full_name
            self._owner.tip.show(text, event.x_root, event.y_root)


# ----------------------------------------------------------------------- список

class VirtualList(ctk.CTkFrame):
    """Прокручуваний список із пулом рядків. Колбеки: on_toggle(program, checked),
    on_open(program), on_uninstall(program)."""

    def __init__(self, master, *, selected: set, on_toggle, on_open, on_uninstall):
        super().__init__(master, fg_color="transparent")
        self.selected = selected  # спільна з вкладкою множина ключів
        self.on_toggle, self.on_open, self.on_uninstall = on_toggle, on_open, on_uninstall
        self.max_size = 0
        self.tip = _Tip(self)
        self.fonts = {
            "name": ctk.CTkFont(family=_FONT_FAMILY, size=13, weight="bold"),
            "small": theme.font_small(),
            "size": ctk.CTkFont(family=_FONT_FAMILY, size=13, weight="bold"),
            "badge": ctk.CTkFont(family=_FONT_FAMILY, size=10, weight="bold"),
        }

        self._items: list[dict] = []
        self._pool: list[ProgramRow] = []
        self._offset = 0.0  # dp
        self._hover_index: int | None = None
        self._hover_job = None
        self._layout_job = None
        self._last_width = 0
        self._measure_fonts: dict = {}
        self._trunc_cache: dict = {}
        self._photo_cache: dict = {}
        self.icons = IconLoader(self._icon_ready_threadsafe)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.viewport = tk.Frame(self, bg=theme.BG_MAIN, bd=0, highlightthickness=0)
        self.viewport.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ctk.CTkScrollbar(
            self, command=self._on_scrollbar, width=12, fg_color="transparent",
            button_color=theme.BORDER, button_hover_color=theme.TEXT_DIM,
        )
        self.scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))

        self.empty_label = ctk.CTkLabel(
            self.viewport, text="", font=theme.font_body(), text_color=theme.TEXT_DIM,
        )

        self.viewport.bind("<Configure>", self._on_configure)
        self.viewport.bind("<Motion>", self.schedule_hover)
        self.viewport.bind("<Leave>", self.schedule_hover)
        self.viewport.bind("<MouseWheel>", self.on_wheel)
        self.bind("<Destroy>", lambda e: self.tip.hide() if e.widget is self else None)

    # ------------------------------------------------------------- дані

    def set_items(self, items: list[dict], *, keep_scroll: bool = False, empty_text: str = "") -> None:
        self._items = items
        if not keep_scroll:
            self._offset = 0.0
        self.tip.hide()
        self.empty_label.configure(text=empty_text)
        for row in self._pool:
            row.index = None
        self._layout()

    def refresh_visible(self) -> None:
        """Перепризначає всі видимі рядки (зміна розмірів, вибору тощо)."""
        for row in self._pool:
            row.index = None
        self._layout()

    def refresh_key(self, key: str) -> None:
        for row in self._pool:
            if row.index is not None and row.program and row.program["key"] == key:
                row.refresh_size(row.program, self.max_size)

    def refresh_selection(self) -> None:
        for row in self._pool:
            if row.index is not None and row.program:
                selected = row.program["key"] in self.selected
                if selected:
                    row.check.select()
                else:
                    row.check.deselect()
                row.set_selected(selected)

    # ---------------------------------------------------------- геометрія

    def _scale(self) -> float:
        return self._get_widget_scaling()

    def _view_dp(self) -> float:
        return self.viewport.winfo_height() / self._scale()

    def _total_dp(self) -> float:
        return len(self._items) * ROW_H

    def _on_configure(self, _event=None) -> None:
        if self._layout_job is None:
            self._layout_job = self.after(16, self._relayout)

    def _relayout(self) -> None:
        self._layout_job = None
        width = self.viewport.winfo_width()
        if width != self._last_width:  # від ширини залежить обрізання назв
            self._last_width = width
            for row in self._pool:
                row.index = None
        self._layout()

    def _ensure_pool(self, view_dp: float) -> None:
        needed = int(view_dp // ROW_H) + 3
        if len(self._pool) >= needed:
            return
        while len(self._pool) < needed:
            self._pool.append(ProgramRow(self.viewport, self))
        for row in self._pool:  # змінився модуль idx % len(pool)
            row.index = None
            row.place_forget()

    def _layout(self) -> None:
        view = self._view_dp()
        if view <= 1:
            return
        self._ensure_pool(view)
        n = len(self._items)
        self._offset = max(0.0, min(self._offset, max(0.0, self._total_dp() - view)))

        first = int(self._offset // ROW_H)
        last = min(n, int((self._offset + view) // ROW_H) + 1)
        pool_n = len(self._pool)
        name_px = self._name_px()

        for idx in range(first, last):
            row = self._pool[idx % pool_n]
            if row.index != idx:
                self._bind_row(row, idx, name_px)
            row.place(x=0, y=idx * ROW_H - self._offset, relwidth=1.0)
        for row in self._pool:
            if row.index is not None and not first <= row.index < last:
                row.place_forget()
                row.index = None
                row._hovered = False

        if n:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=0.5, rely=0.35, anchor="center")
        self._update_scrollbar()
        self.schedule_hover()

    def _bind_row(self, row: ProgramRow, idx: int, name_px: int) -> None:
        program = self._items[idx]
        row.bind_program(
            idx, program, name_px=name_px, selected=program["key"] in self.selected,
            hovered=idx == self._hover_index, max_size=self.max_size, icon=self.icon_photo(program),
        )

    def _name_px(self) -> int:
        fixed = (COL_CHECK + COL_ICON + COL_BADGE + COL_SIZE + COL_RIGHT + _ROW_EXTRA_DP) * self._scale()
        return max(int(self.viewport.winfo_width() - fixed), 90)

    def truncate(self, text: str, max_px: int, kind: str) -> str:
        cache_key = (text, max_px, kind, round(self._scale(), 3))
        cached = self._trunc_cache.get(cache_key)
        if cached is not None:
            return cached
        font = self._measure_font(kind)
        if font.measure(text) <= max_px:
            result = text
        else:
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if font.measure(text[:mid].rstrip() + "…") <= max_px:
                    lo = mid
                else:
                    hi = mid - 1
            result = text[:lo].rstrip() + "…"
        if len(self._trunc_cache) > 4000:
            self._trunc_cache.clear()
        self._trunc_cache[cache_key] = result
        return result

    def _measure_font(self, kind: str) -> tkfont.Font:
        px = round((13 if kind == "name" else 11) * self._scale())
        key = (kind, px)
        font = self._measure_fonts.get(key)
        if font is None:
            font = tkfont.Font(family=_FONT_FAMILY, size=-px, weight="bold" if kind == "name" else "normal")
            self._measure_fonts[key] = font
        return font

    # ---------------------------------------------------------- прокрутка

    def _update_scrollbar(self) -> None:
        total, view = self._total_dp(), self._view_dp()
        if total <= view or total <= 0:
            self.scrollbar.set(0, 1)
        else:
            self.scrollbar.set(self._offset / total, (self._offset + view) / total)

    def _on_scrollbar(self, *args) -> None:
        total, view = self._total_dp(), self._view_dp()
        if args[0] == "moveto":
            self._offset = float(args[1]) * total
        elif args[0] == "scroll":
            step = ROW_H if args[2] == "units" else view * 0.9
            self._offset += int(args[1]) * step
        self._layout()

    def on_wheel(self, event) -> None:
        self._offset -= (event.delta / 120) * ROW_H * 1.2
        self._layout()

    # ----------------------------------------------------------- наведення

    def schedule_hover(self, _event=None) -> None:
        if self._hover_job is None:
            self._hover_job = self.after_idle(self._update_hover)

    def _update_hover(self) -> None:
        self._hover_job = None
        if not self.winfo_exists():
            return
        S = self._scale()
        vp = self.viewport
        px = vp.winfo_pointerx() - vp.winfo_rootx()
        py = vp.winfo_pointery() - vp.winfo_rooty()
        index = None
        if 0 <= px < vp.winfo_width() and 0 <= py < vp.winfo_height() and vp.winfo_ismapped():
            y = py / S + self._offset
            if y % ROW_H < CARD_H and int(y // ROW_H) < len(self._items):
                index = int(y // ROW_H)
        if index == self._hover_index:
            return
        old, self._hover_index = self._hover_index, index
        for row in self._pool:
            if row.index is not None and row.index in (old, index):
                row.set_hovered(row.index == index)
        if index is None:
            self.tip.hide()

    # -------------------------------------------------------------- іконки

    def icon_photo(self, program: dict):
        """PhotoImage іконки програми (реальна, якщо вже витягнута, інакше заглушка)."""
        S = self._scale()
        ready, image = self.icons.get(program["key"])
        if not ready:
            self.icons.request(program)
        kind = "img" if image is not None else "ph"
        cache_key = (program["key"], kind, round(S, 3))
        photo = self._photo_cache.get(cache_key)
        if photo is None:
            px = round(ICON_DP * S)
            if image is not None:
                img = image.resize((px, px), Image.Resampling.LANCZOS)
            else:
                img = _placeholder_image(program["name"], px)
            photo = ImageTk.PhotoImage(img)
            self._photo_cache[cache_key] = photo
        return photo

    def _icon_ready_threadsafe(self, key: str) -> None:
        try:
            self.after(0, self._on_icon_ready, key)
        except (RuntimeError, tk.TclError):
            pass

    def _on_icon_ready(self, key: str) -> None:
        if not self.winfo_exists():
            return
        for row in self._pool:
            if row.index is not None and row.program and row.program["key"] == key:
                row.set_icon(self.icon_photo(row.program))
