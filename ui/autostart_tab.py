"""Вкладка «Автозапуск» — програми з реєстру Run і папок Startup, увімк./вимк."""

from tkinter import messagebox

import customtkinter as ctk

from core import autostart as autostart_core
from ui import theme
from ui.widgets.canvas_list import CanvasList, card_image, pill_image, switch_image

_SOURCE_ORDER = (
    autostart_core.SOURCE_HKCU,
    autostart_core.SOURCE_HKLM,
    autostart_core.SOURCE_HKLM32,
    autostart_core.SOURCE_STARTUP_USER,
    autostart_core.SOURCE_STARTUP_COMMON,
)

_IMPACT_COLORS = {
    "висока": "#e5484d",
    "середня": "#e0a52f",
    "низька": "#8a8a8a",
}

_CARD_PAD = 10  # dp: внутрішній відступ картки зверху/знизу
_ROW_GAP = 8
_HEADER_H = 38
_SWITCH_W, _SWITCH_H = 40, 20
_TEXT_X = 14 + _SWITCH_W + 12
_OPEN_W, _BTN_H = 150, 26
_LINE_SMALL = 16


class AutostartList(CanvasList):
    """Записи автозапуску, згруповані за джерелом, на одному Canvas.

    Рядок — або заголовок групи, або картка запису: перемикач, назва з
    впливом, видавець, команда (з переносом), попередження античиту / прав
    і кнопки. Висота картки залежить від переносу тексту й рахується один
    раз на ширину (кеш), тож прокрутка лише зсуває готові рядки."""

    clickable_regions = frozenset({"switch", "open"})
    sound_regions = frozenset({"open"})

    def __init__(self, master, *, on_toggle, on_open_location):
        super().__init__(master, bg=theme.BG_MAIN)
        self._on_toggle, self._on_open_location = on_toggle, on_open_location
        self.rows: list[tuple] = []  # ("header", source) | ("entry", entry)
        self._layouts: dict = {}
        self._probe = self.canvas.create_text(-10000, -10000, anchor="nw", text="")

    def set_rows(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.set_count(len(rows), keep_scroll=True)

    def on_width_changed(self) -> None:
        self._layouts.clear()

    def on_scale_changed(self) -> None:
        self._layouts.clear()

    # ------------------------------------------------------- розкладка

    def _text_w(self) -> int:
        return max(self.width - self.px(_TEXT_X + 14 + _OPEN_W + 12), self.px(120))

    def _text_height(self, text: str, font: tuple, width: int) -> int:
        c = self.canvas
        c.itemconfigure(self._probe, text=text, font=font, width=width)
        _x0, y0, _x1, y1 = c.bbox(self._probe)
        return y1 - y0

    def _layout_for(self, entry: dict) -> dict:
        """y-позиції (px відносно картки) блоків тексту й висота картки."""
        key = entry["id"]
        lay = self._layouts.get(key)
        if lay is not None:
            return lay
        small = self.font(11)
        text_w = self._text_w()
        y = self.px(_CARD_PAD)
        lay = {"title": y}
        y += self.px(20)
        if entry.get("publisher"):
            lay["publisher"] = y
            y += self.px(_LINE_SMALL)
        lay["command"] = y
        y += self._text_height(entry["command"], small, text_w)
        if entry["is_anticheat"]:
            y += self.px(2)
            lay["anticheat"] = y
            y += self._text_height(autostart_core.ANTICHEAT_WARNING, small, text_w)
        lay["card_h"] = max(y + self.px(_CARD_PAD), self.px(_CARD_PAD * 2 + _BTN_H + 12))
        self._layouts[key] = lay
        return lay

    def row_height_dp(self, index: int) -> float:
        kind, value = self.rows[index]
        if kind == "header":
            return _HEADER_H
        return (self._layout_for(value)["card_h"] + self.px(_ROW_GAP)) / self.S

    def _open_box(self):
        x1 = self.width - self.px(14)
        y0 = self.px(_CARD_PAD - 2)
        return x1 - self.px(_OPEN_W), y0, x1, y0 + self.px(_BTN_H)

    def _switch_box(self, lay):
        x0 = self.px(14)
        y0 = lay["title"] + (self.px(20) - self.px(_SWITCH_H)) // 2
        return x0, y0, x0 + self.px(_SWITCH_W), y0 + self.px(_SWITCH_H)

    # ---------------------------------------------------------- рядки

    def create_slot(self, slot) -> None:
        c = self.canvas
        opt = (slot.tag,)
        it = slot.items
        small = self.font(11)
        it["header"] = c.create_text(0, 0, anchor="sw", fill=theme.TEXT_MAIN, font=self.font(14, "bold"), tags=opt)
        it["bg"] = c.create_image(0, 0, anchor="nw", tags=opt)
        it["switch"] = c.create_image(0, 0, anchor="nw", tags=opt)
        it["name"] = c.create_text(0, 0, anchor="w", font=self.font(13, "bold"), tags=opt)
        it["impact"] = c.create_text(0, 0, anchor="w", font=small, tags=opt)
        it["publisher"] = c.create_text(0, 0, anchor="nw", fill=theme.TEXT_DIM, font=small, tags=opt)
        it["command"] = c.create_text(0, 0, anchor="nw", fill=theme.TEXT_DIM, font=small, tags=opt)
        it["anticheat"] = c.create_text(0, 0, anchor="nw", fill=theme.WARNING, font=small,
                                        text=autostart_core.ANTICHEAT_WARNING, tags=opt)
        for name, text in (("open", "Відкрити розташування"),):
            it[f"{name}_bg"] = c.create_image(0, 0, anchor="nw", tags=opt)
            it[f"{name}_text"] = c.create_text(0, 0, anchor="center", text=text, font=small,
                                               fill=theme.TEXT_MAIN, tags=opt)

    def bind_slot(self, slot, index: int) -> None:
        kind, value = self.rows[index]
        c = self.canvas
        it = slot.items

        def show(*names):
            for name in names:
                c.itemconfigure(it[name], state="normal")

        if kind == "header":
            c.coords(it["header"], self.px(4), self.px(_HEADER_H - 8))
            c.itemconfigure(it["header"], text=autostart_core.SOURCE_LABELS[value])
            show("header")
            return

        entry = value
        lay = self._layout_for(entry)
        x = self.px(_TEXT_X)
        text_w = self._text_w()
        title_y = lay["title"] + self.px(10)

        impact = entry.get("impact", "низька")
        extra = f"  ·  Вплив: {impact}" + ("  ·  системний" if entry["is_system"] else "")
        name_font = self.font(13, "bold")
        name = self.truncate(
            entry["display_name"], max(text_w - self.text_width(extra, self.font(11)), self.px(60)), name_font,
        )
        c.coords(it["name"], x, title_y)
        c.itemconfigure(it["name"], text=name, fill=theme.TEXT_DIM if entry["is_system"] else theme.TEXT_MAIN)
        c.coords(it["impact"], x + self.text_width(name, name_font), title_y)
        c.itemconfigure(it["impact"], text=extra, fill=_IMPACT_COLORS.get(impact, theme.TEXT_DIM))
        show("bg", "switch", "name", "impact")

        if "publisher" in lay:
            c.coords(it["publisher"], x, lay["publisher"])
            c.itemconfigure(it["publisher"], text=self.truncate(entry["publisher"], text_w, self.font(11)))
            show("publisher")
        c.coords(it["command"], x, lay["command"])
        c.itemconfigure(it["command"], text=entry["command"], width=text_w)
        show("command")
        if "anticheat" in lay:
            c.coords(it["anticheat"], x, lay["anticheat"])
            c.itemconfigure(it["anticheat"], width=text_w)
            show("anticheat")
        x0, y0, x1, y1 = self._open_box()
        c.coords(it["open_bg"], x0, y0)
        c.coords(it["open_text"], (x0 + x1) / 2, (y0 + y1) / 2)
        c.itemconfigure(it["open_text"], fill=theme.TEXT_MAIN if entry.get("resolved_path") else theme.TEXT_DIM)
        show("open_bg", "open_text")
        c.coords(it["switch"], *self._switch_box(lay)[:2])

    def hover_slot(self, slot, index: int, region) -> None:
        kind, entry = self.rows[index]
        if kind != "entry":
            return
        lay = self._layout_for(entry)
        c = self.canvas
        it = slot.items
        hovered = region is not None
        c.itemconfigure(it["bg"], image=card_image(
            max(self.width, 40), lay["card_h"], self.px(10),
            theme.BG_PANEL_LIGHT if hovered else theme.BG_PANEL,
            theme.ACCENT_BLUE if hovered else theme.BORDER, self.bg, max(1, self.px(1)),
        ))
        c.itemconfigure(it["switch"], image=switch_image(
            self.px(_SWITCH_W), self.px(_SWITCH_H), entry["enabled"], region == "switch", False,
        ))
        if entry.get("resolved_path"):
            open_color = "#2d3953" if region == "open" else theme.BORDER
        else:
            open_color = theme.BG_PANEL_LIGHT  # недоступна кнопка
        c.itemconfigure(it["open_bg"], image=pill_image(self.px(_OPEN_W), self.px(_BTN_H), open_color, self.px(8)))

    def hit_test(self, index: int, x: int, y: int):
        kind, entry = self.rows[index]
        if kind == "header":
            return None
        lay = self._layout_for(entry)
        if y >= lay["card_h"]:
            return None

        def inside(box):
            return box[0] <= x < box[2] and box[1] <= y < box[3]

        if inside(self._switch_box(lay)):
            return "switch"
        if entry.get("resolved_path") and inside(self._open_box()):
            return "open"
        return "row"

    def click(self, index: int, region: str) -> None:
        _kind, entry = self.rows[index]
        if region == "switch":
            self._on_toggle(entry)
            self.refresh_index(index)
        elif region == "open":
            self._on_open_location(entry)


class AutostartTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.all_entries: list[dict] = []

        self._build_header()

        self.list = AutostartList(
            self, on_toggle=self._on_row_toggle, on_open_location=self._on_open_location,
        )
        self.list.grid(row=1, column=0, sticky="nsew", padx=(20, 14), pady=(0, 16))

        self._load()

    # ------------------------------------------------------------------ UI

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="Автозапуск", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )

        controls = ctk.CTkFrame(header, fg_color="transparent")
        controls.grid(row=0, column=1, sticky="e")

        self.search_var = ctk.StringVar()
        self.search_entry = ctk.CTkEntry(
            controls, placeholder_text="Пошук за назвою", width=220, textvariable=self.search_var,
        )
        self.search_entry.pack(side="left", padx=(0, 10))
        self.search_var.trace_add("write", lambda *_a: self._render())

        ctk.CTkButton(controls, text="Оновити", width=100, command=self._load).pack(side="left")

        self.status_label = ctk.CTkLabel(header, text="", text_color="gray")
        self.status_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    # ----------------------------------------------------------------- load

    def _load(self):
        self.all_entries = autostart_core.list_entries()
        self._render()

    def _query(self) -> str:
        return self.search_var.get().strip().lower()

    def _filtered(self) -> list[dict]:
        query = self._query()
        return [
            e for e in self.all_entries
            if not query
            or query in e["display_name"].lower()
            or query in e["name"].lower()
            or query in (e.get("publisher") or "").lower()
        ]

    def _render(self):
        if not self.all_entries:
            self.list.set_empty_text("Не знайдено жодної програми автозапуску.")
            self.list.set_rows([])
            self.status_label.configure(text="Знайдено записів: 0")
            return

        entries = self._filtered()
        by_source: dict[str, list[dict]] = {}
        for entry in entries:
            by_source.setdefault(entry["source"], []).append(entry)
        rows = []
        for source in _SOURCE_ORDER:
            source_entries = by_source.get(source)
            if source_entries:
                rows.append(("header", source))
                rows.extend(("entry", entry) for entry in source_entries)
        self.list.set_empty_text("Нічого не знайдено за пошуком.")
        self.list.set_rows(rows)
        self._update_status(len(entries))

    def _update_status(self, found: int | None = None) -> None:
        enabled_count = sum(1 for e in self.all_entries if e["enabled"])
        status = f"Увімкнено {enabled_count} з {len(self.all_entries)}"
        if self._query():
            if found is None:
                found = len(self._filtered())
            status += f"  ·  Знайдено за пошуком: {found}"
        self.status_label.configure(text=status)

    # --------------------------------------------------------------- toggle

    def _on_row_toggle(self, entry: dict):
        want_enabled = not entry["enabled"]
        if not want_enabled and not self._confirm_disable(entry):
            return

        if want_enabled:
            success, error = autostart_core.enable_entry(entry["id"])
        else:
            success, error = autostart_core.disable_entry(entry)

        if success:
            entry["enabled"] = want_enabled
            self._update_status()
        else:
            messagebox.showerror("Помилка", error or "Не вдалося змінити стан автозапуску", parent=self)

    def _confirm_disable(self, entry: dict) -> bool:
        name = entry["display_name"]
        if entry["is_anticheat"]:
            return messagebox.askyesno(
                "Підтвердження",
                f"«{name}» пов'язаний з античитом.\n{autostart_core.ANTICHEAT_WARNING}\n\nВимкнути автозапуск?",
                parent=self,
            )
        if entry["is_system"]:
            return messagebox.askyesno(
                "Підтвердження",
                f"«{name}» — системний запис Windows. Вимикати його зазвичай не потрібно.\n\n"
                "Вимкнути автозапуск?",
                parent=self,
            )
        return True

    # --------------------------------------------------------- open location

    def _on_open_location(self, entry: dict):
        success, error = autostart_core.open_location(entry)
        if not success:
            messagebox.showerror("Помилка", error or "Не вдалося відкрити розташування файлу", parent=self)
