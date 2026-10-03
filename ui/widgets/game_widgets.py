"""Віджети вкладки «Ігровий режим»: великий перемикач, чіпи програм,
списки ігор і сесій та вертикально прокручувана сторінка.

Як і на «Моніторі», нічого «важкого»: перемикач — спрайт Pillow, чіпи —
один Canvas, списки — CanvasList (віртуалізовані), а не сотні CTk-віджетів.
"""

from __future__ import annotations

import time
import tkinter as tk
import tkinter.font as tkfont

import customtkinter as ctk
from PIL import Image, ImageTk

from core import game_sessions
from core.app_icons import IconLoader
from ui import bg, theme
from ui.widgets import scroll
from ui.widgets.canvas_list import (
    CanvasList, FastScrollbar, Tooltip, card_image, switch_image,
)
from core.i18n import t



def fmt_mem(mb: float) -> str:
    return t("units.gb_1", v=mb / 1024) if mb >= 1024 else t("units.mb_0", mb=mb)


# ================================================================== база

class CanvasBox(ctk.CTkFrame):
    """Прозорий CTk-контейнер із одним tk.Canvas, що знає масштаб екрана (DPI)."""

    def __init__(self, master, width_dp: float, height_dp: float, bg: str):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.S = self._get_widget_scaling()
        self.bg = bg
        self._size_dp = (width_dp, height_dp)
        self.canvas = tk.Canvas(
            self, width=round(width_dp * self.S), height=round(height_dp * self.S),
            bg=bg, highlightthickness=0, bd=0, takefocus=0,
        )
        self.canvas.pack()

    def px(self, dp: float) -> int:
        return round(dp * self.S)

    def _set_scaling(self, *args, **kwargs):
        super()._set_scaling(*args, **kwargs)
        if not hasattr(self, "canvas"):
            return
        self.S = args[0]
        if self._size_dp[0]:
            self.canvas.configure(width=self.px(self._size_dp[0]), height=self.px(self._size_dp[1]))
        self.on_scale_changed()

    def on_scale_changed(self) -> None:
        pass


class IconCache:
    """Іконки exe для canvas-віджетів: фонове витягування (IconLoader) + PhotoImage
    потрібного розміру. on_update() (у потоці UI) — коли з'явилась нова іконка."""

    def __init__(self, owner: tk.Misc, size_dp: int, on_update):
        self._owner = owner
        self._size_dp = size_dp
        self._on_update = on_update
        self._photos: dict = {}
        self._loader = IconLoader(self._ready_threadsafe)

    def get(self, path: str | None, scale: float):
        if not path:
            return None
        ready, image = self._loader.get(path)
        if not ready:
            self._loader.request({"key": path, "display_icon": (path,)})
            return None
        if image is None:
            return None
        key = (path, round(scale, 3))
        photo = self._photos.get(key)
        if photo is None:
            size = round(self._size_dp * scale)
            photo = self._photos[key] = ImageTk.PhotoImage(image.resize((size, size), Image.Resampling.LANCZOS))
        return photo

    def clear_scaled(self) -> None:
        self._photos.clear()

    def _ready_threadsafe(self, _key: str) -> None:
        bg.ui_call(self._owner, self._on_update)


# ============================================================ перемикач

class BigSwitch(CanvasBox):
    """Великий перемикач. Клік → command(); busy — доки триває вмикання."""

    W, H = 104, 56

    def __init__(self, master, command, bg: str = theme.BG_PANEL):
        super().__init__(master, self.W, self.H, bg)
        self._command = command
        self._on = False
        self._busy = False
        self._hover = False
        self._item = self.canvas.create_image(0, 0, anchor="nw")
        c = self.canvas
        c.bind("<Enter>", lambda _e: self._set_hover(True))
        c.bind("<Leave>", lambda _e: self._set_hover(False))
        c.bind("<ButtonRelease-1>", self._on_release)
        self._redraw()

    def set_on(self, on: bool) -> None:
        if on != self._on:
            self._on = on
            self._redraw()

    def set_busy(self, busy: bool) -> None:
        if busy != self._busy:
            self._busy = busy
            self._redraw()

    def on_scale_changed(self) -> None:
        self._redraw()

    def _set_hover(self, hover: bool) -> None:
        self._hover = hover
        self.canvas.configure(cursor="" if self._busy else "hand2")
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.itemconfigure(self._item, image=switch_image(
            self.px(self.W), self.px(self.H), self._on, self._hover and not self._busy, self._busy,
        ))

    def _on_release(self, event) -> None:
        if self._busy or not (0 <= event.x < self.px(self.W) and 0 <= event.y < self.px(self.H)):
            return
        theme.play_click()
        self._command()


# ================================================================= чіпи

_CHIP_H, _CHIP_GAP, _CHIP_PAD, _CHIP_ICON = 32, 8, 10, 16


class ChipBoard(CanvasBox):
    """Чіпи «іконка · назва · RAM · ✕», що переносяться на новий рядок. Один Canvas,
    що перебудовується лише при зміні даних чи ширини. Понад max_rows рядків —
    останній чіп «ще N» (не зникає, просто згортає решту)."""

    def __init__(self, master, on_remove, max_rows: int = 3, bg: str = theme.BG_PANEL):
        super().__init__(master, 0, 40, bg)
        self.canvas.pack_forget()
        self.canvas.pack(fill="x")
        self._on_remove = on_remove
        self._max_rows = max_rows
        self._chips: list[dict] = []
        self._removable = True
        self._empty_text = ""
        self._layout: list[dict] = []
        self._hover = None  # (індекс, "x"|"chip")
        self._width = 0
        self._job = None
        self._fonts: dict = {}
        self._measure: dict = {}
        self._tooltip = Tooltip(self)
        self._icons = IconCache(self, _CHIP_ICON, self._icons_ready)
        c = self.canvas
        c.bind("<Configure>", self._on_configure)
        c.bind("<Motion>", self._on_motion)
        c.bind("<Leave>", lambda _e: self._set_hover(None))
        c.bind("<ButtonRelease-1>", self._on_release)

    # ------------------------------------------------------------ дані

    def set_chips(self, chips: list[dict], removable: bool, empty_text: str) -> None:
        signature = [(c["key"], round(c["memory_mb"] / 16)) for c in chips]
        if signature == [(c["key"], round(c["memory_mb"] / 16)) for c in self._chips] \
                and removable == self._removable and empty_text == self._empty_text:
            self._chips = chips  # той самий вигляд — лише свіжі дані для підказок
            return
        self._chips, self._removable, self._empty_text = chips, removable, empty_text
        self._rebuild()

    def on_scale_changed(self) -> None:
        self._fonts.clear()
        self._measure.clear()
        self._icons.clear_scaled()
        self._rebuild()

    def _font(self, size: int, weight: str = "normal") -> tuple:
        key = (size, weight)
        if key not in self._fonts:
            self._fonts[key] = (theme.font_family(), -round(size * self.S), weight)
        return self._fonts[key]

    def _text_w(self, text: str, font: tuple) -> int:
        m = self._measure.get(font)
        if m is None:
            m = self._measure[font] = tkfont.Font(family=font[0], size=font[1], weight=font[2])
        return m.measure(text)

    def _truncate(self, text: str, max_px: int, font: tuple) -> str:
        if self._text_w(text, font) <= max_px:
            return text
        while len(text) > 1 and self._text_w(text.rstrip() + "…", font) > max_px:
            text = text[:-1]
        return text.rstrip() + "…"

    # ---------------------------------------------------------- розкладка

    def _on_configure(self, event) -> None:
        if event.width == self._width:
            return
        self._width = event.width
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(40, self._rebuild)

    def _measure_chip(self, chip: dict) -> dict:
        title_font, ram_font = self._font(12), self._font(11)
        title = self._truncate(chip["title"], self.px(170), title_font)
        ram = fmt_mem(chip["memory_mb"]) if chip.get("memory_mb") else ""
        w = self.px(_CHIP_PAD) + (self.px(_CHIP_ICON + 6) if chip.get("icon", True) else 0)
        w += self._text_w(title, title_font)
        if ram:
            w += self.px(8) + self._text_w(ram, ram_font)
        w += self.px(_CHIP_PAD)
        if chip.get("removable"):
            w += self.px(8 + 14)
        return {"chip": chip, "title": title, "ram": ram, "w": w}

    def _place(self, measured: list[dict], width: int) -> tuple[int, int]:
        """Проставляє x/y чіпам, повертає (кількість рядків, висота px)."""
        x = y = 0
        rows = 1
        h, gap = self.px(_CHIP_H), self.px(_CHIP_GAP)
        for m in measured:
            if x and x + m["w"] > width:
                x, y, rows = 0, y + h + gap, rows + 1
            m["x"], m["y"] = x, y
            x += m["w"] + gap
        return rows, y + h

    def _rebuild(self) -> None:
        self._job = None
        width = self._width or self.canvas.winfo_width()
        c = self.canvas
        c.delete("all")
        self._layout = []
        self._hover = None
        if width <= 1:
            return
        if not self._chips:
            c.configure(height=self.px(30))
            c.create_text(0, self.px(15), anchor="w", text=self._empty_text, fill=theme.TEXT_DIM,
                          font=self._font(13))
            return

        for chip in self._chips:
            chip["removable"] = self._removable
        measured = [self._measure_chip(chip) for chip in self._chips]
        rows, height = self._place(measured, width)
        if rows > self._max_rows:
            hidden = 0
            while measured and rows > self._max_rows:
                measured.pop()
                hidden += 1
                more = self._measure_chip({
                    "key": None, "title": t("game_widgets.more", count=hidden), "memory_mb": 0, "icon": False, "removable": False,
                    "hidden_titles": [ch["title"] for ch in self._chips[len(measured):]],
                })
                rows, height = self._place(measured + [more], width)
            measured.append(more)
        c.configure(height=height)

        h = self.px(_CHIP_H)
        for i, m in enumerate(measured):
            chip, x, y = m["chip"], m["x"], m["y"]
            mid = y + h // 2
            item = {"m": m, "x": x, "y": y, "w": m["w"], "h": h}
            item["bg"] = c.create_image(x, y, anchor="nw", image=self._bg(m["w"], False))
            tx = x + self.px(_CHIP_PAD)
            if chip.get("icon", True):
                item["icon"] = c.create_image(tx, mid, anchor="w")
                item["icon_path"] = chip.get("exe_path")
                self._set_icon(item)
                tx += self.px(_CHIP_ICON + 6)
            item["title"] = c.create_text(tx, mid, anchor="w", text=m["title"], fill=theme.TEXT_MAIN,
                                          font=self._font(12))
            tx += self._text_w(m["title"], self._font(12)) + self.px(8)
            if m["ram"]:
                c.create_text(tx, mid, anchor="w", text=m["ram"], fill=theme.TEXT_DIM, font=self._font(11))
            if chip.get("removable"):
                item["x_box"] = (x + m["w"] - self.px(_CHIP_PAD + 14), y, x + m["w"], y + h)
                item["cross"] = c.create_text(x + m["w"] - self.px(_CHIP_PAD + 2), mid, anchor="center", text="✕",
                                              fill=theme.TEXT_DIM, font=self._font(11, "bold"))
            self._layout.append(item)

    def _bg(self, w: int, hover: bool):
        return card_image(w, self.px(_CHIP_H), self.px(_CHIP_H // 2),
                          "#222d45" if hover else theme.BG_PANEL_LIGHT, theme.BORDER, self.bg)

    def _set_icon(self, item: dict) -> None:
        photo = self._icons.get(item.get("icon_path"), self.S)
        if photo is not None:
            self.canvas.itemconfigure(item["icon"], image=photo)
        else:
            self.canvas.itemconfigure(item["icon"], image="")

    def _icons_ready(self) -> None:
        for item in self._layout:
            if "icon" in item:
                self._set_icon(item)

    # ------------------------------------------------------------ події

    def _locate(self, x: int, y: int):
        for i, item in enumerate(self._layout):
            if item["x"] <= x < item["x"] + item["w"] and item["y"] <= y < item["y"] + item["h"]:
                box = item.get("x_box")
                if box and box[0] <= x < box[2]:
                    return i, "x"
                return i, "chip"
        return None

    def _on_motion(self, event) -> None:
        self._set_hover(self._locate(event.x, event.y))

    def _set_hover(self, hover) -> None:
        if hover == self._hover:
            return
        old, self._hover = self._hover, hover
        c = self.canvas
        if old is not None and old[0] < len(self._layout):
            item = self._layout[old[0]]
            c.itemconfigure(item["bg"], image=self._bg(item["w"], False))
            if "cross" in item:
                c.itemconfigure(item["cross"], fill=theme.TEXT_DIM)
        self._tooltip.hide()
        c.configure(cursor="")
        if hover is None:
            return
        item = self._layout[hover[0]]
        c.itemconfigure(item["bg"], image=self._bg(item["w"], True))
        if "cross" in item:
            c.itemconfigure(item["cross"], fill=theme.ERROR if hover[1] == "x" else theme.TEXT_MAIN)
        if hover[1] == "x":
            c.configure(cursor="hand2")
        text = self._tooltip_text(item["m"]["chip"], hover[1])
        if text:
            self._tooltip.schedule(text)

    def _tooltip_text(self, chip: dict, region: str) -> str | None:
        if chip.get("hidden_titles"):
            return t("game_widgets.also_closes") + "\n".join(chip["hidden_titles"])
        if region == "x":
            return t("game_widgets.dont_close")
        lines = [chip["title"]]
        meta = " · ".join(filter(None, [
            chip.get("category_label"),
            t("game_widgets.processes", count=chip['count']) if chip.get("count") else None,
            fmt_mem(chip["memory_mb"]) if chip.get("memory_mb") else None,
        ]))
        if meta:
            lines.append(meta)
        if chip.get("exe_path"):
            lines.append(chip["exe_path"])
        if chip.get("note"):
            lines.append(chip["note"])
        return "\n".join(lines)

    def _on_release(self, event) -> None:
        hit = self._locate(event.x, event.y)
        if hit is not None and hit[1] == "x":
            theme.play_click()
            self._on_remove(self._layout[hit[0]]["m"]["chip"]["key"])


# ================================================================= ігри

_GAME_ROW_DP, _GAME_CARD_DP, _GAME_ICON_DP = 54, 48, 30
_GROUP_ROW_DP = 34
_SW_W, _SW_H = 40, 22


class GamesList(CanvasList):
    """Знайдені ігри: іконка, назва, платформа, перемикач «вмикати режим автоматично».
    Рядок із "group" — заголовок групи «Інше» (клік — згорнути/розгорнути)."""

    wheel_step_dp = _GAME_ROW_DP * 2
    clickable_regions = frozenset({"switch", "group"})
    sound_regions = frozenset({"group"})

    def __init__(self, master, on_toggle, on_group=None):
        super().__init__(master, bg=theme.BG_PANEL, scrollbar_gap=4)
        self._on_toggle = on_toggle
        self._on_group = on_group
        self.items: list[dict] = []  # key, name, platform, exe, auto, running | group, expanded
        self.icons = IconCache(self, _GAME_ICON_DP, self.update_visible)
        self._hover_fill = theme.lerp_color(self, theme.BG_PANEL_LIGHT, "#ffffff", 0.05)

    def set_items(self, items: list[dict]) -> None:
        keep = [i["key"] for i in self.items] == [i["key"] for i in items]
        self.items = items
        if keep:
            self.update_visible()
        else:
            self.set_count(len(items), keep_scroll=True)

    def row_height_dp(self, index: int) -> float:
        return _GROUP_ROW_DP if self.items[index].get("group") else _GAME_ROW_DP

    def on_scale_changed(self) -> None:
        self.icons.clear_scaled()

    def create_slot(self, slot) -> None:
        c = self.canvas
        base = (slot.tag, slot.base_tag)
        it = slot.items
        it["bg"] = c.create_image(0, 0, anchor="nw", tags=base)
        it["icon"] = c.create_image(0, 0, anchor="w", tags=(slot.tag,))
        it["name"] = c.create_text(0, 0, anchor="sw", font=self.font(13, "bold"), fill=theme.TEXT_MAIN, tags=base)
        it["sub"] = c.create_text(0, 0, anchor="nw", font=self.font(11), fill=theme.TEXT_DIM, tags=base)
        it["run"] = c.create_text(0, 0, anchor="nw", font=self.font(11, "bold"), fill=theme.ACCENT_GREEN, tags=(slot.tag,))
        it["switch"] = c.create_image(0, 0, anchor="e", tags=base)
        it["auto"] = c.create_text(0, 0, anchor="e", font=self.font(11), fill=theme.TEXT_DIM, text=t("game_widgets.auto"), tags=base)
        it["group"] = c.create_text(0, 0, anchor="w", font=self.font(12, "bold"), fill=theme.TEXT_DIM, tags=(slot.tag,))

    def _card_box(self):
        return self.px(_GAME_CARD_DP)

    def _bind_group(self, slot, item: dict) -> None:
        c = self.canvas
        for name in ("bg", "name", "sub", "switch", "auto"):
            c.itemconfigure(slot.items[name], state="hidden")
        mid = self.px(_GROUP_ROW_DP) // 2 + self.px(2)
        arrow = "▾" if item["expanded"] else "▸"
        self.icoords(slot, "group", self.px(4), mid)
        self.iset(slot, "group", text=f"{arrow}  {item['name']}  ·  {item['count']}", state="normal")

    def bind_slot(self, slot, index: int) -> None:
        item = self.items[index]
        if item.get("group"):
            self._bind_group(slot, item)
            return
        w, card = self.width, self.px(_GAME_CARD_DP)
        mid = card // 2
        self.icoords(slot, "bg", 0, 0)
        photo = self.icons.get(item.get("exe"), self.S)
        if photo is not None:
            self.icoords(slot, "icon", self.px(10), mid)
            self.iset(slot, "icon", image=photo, state="normal")
        else:
            self.iset(slot, "icon", state="hidden")
        tx = self.px(10 + _GAME_ICON_DP + 10)
        right = w - self.px(10 + _SW_W + 46)
        font = self.font(13, "bold")
        self.icoords(slot, "name", tx, mid + self.px(1))
        self.iset(slot, "name", text=self.truncate(item["name"], max(right - tx, self.px(40)), font))
        self.icoords(slot, "sub", tx, mid + self.px(2))
        self.iset(slot, "sub", text=item["platform"])
        if item.get("running"):
            self.icoords(slot, "run", tx + self.text_width(item["platform"], self.font(11)) + self.px(8), mid + self.px(2))
            self.iset(slot, "run", text=t("game_widgets.running"), state="normal")
        else:
            self.iset(slot, "run", state="hidden")
        self.icoords(slot, "switch", w - self.px(10), mid)
        self.icoords(slot, "auto", w - self.px(10 + _SW_W + 8), mid)
        self.iset(slot, "auto", fill=theme.ACCENT_GREEN if item["auto"] else theme.TEXT_DIM)

    def hover_slot(self, slot, index: int, region) -> None:
        item = self.items[index]
        if item.get("group"):
            self.iset(slot, "group", fill=theme.TEXT_MAIN if region else theme.TEXT_DIM)
            return
        self.iset(slot, "bg", image=card_image(
            max(self.width, 20), self.px(_GAME_CARD_DP), self.px(10),
            self._hover_fill if region else theme.BG_PANEL_LIGHT, theme.BORDER, theme.BG_PANEL,
        ))
        self.iset(slot, "switch", image=switch_image(
            self.px(_SW_W), self.px(_SW_H), item["auto"], region == "switch", False,
        ))

    def _switch_box(self):
        w, h = self.px(_SW_W), self.px(_SW_H)
        x1 = self.width - self.px(10)
        y0 = (self.px(_GAME_CARD_DP) - h) // 2
        return x1 - w - self.px(6), y0 - self.px(6), x1 + self.px(4), y0 + h + self.px(6)

    def hit_test(self, index: int, x: int, y: int):
        if self.items[index].get("group"):
            return "group"
        if y >= self.px(_GAME_CARD_DP):
            return None  # проміжок між картками
        x0, y0, x1, y1 = self._switch_box()
        return "switch" if x0 <= x < x1 and y0 <= y < y1 else "row"

    def click(self, index: int, region: str) -> None:
        if region == "group":
            if self._on_group is not None:
                self._on_group()
            return
        if region != "switch":
            return
        item = self.items[index]
        item["auto"] = not item["auto"]
        slot = self.slot_for(index)
        if slot is not None:
            self.bind_slot(slot, index)
            self.hover_slot(slot, index, region)
        self._on_toggle(item["key"], item["auto"])

    def row_identity(self, index: int):
        return self.items[index]["key"]

    def tooltip_for(self, index: int, region: str):
        item = self.items[index]
        if region == "group":
            return (t("game_widgets.other_group_tip"))
        if region == "switch":
            return t("game_widgets.auto_tip")
        return f"{item['name']}\n{item['platform']}\n{item.get('folder', '')}"


# ================================================================ сесії

_SESSION_ROW_DP, _SESSION_CARD_DP = 50, 44


def fmt_pct(value) -> str:
    return "—" if value is None else f"{value:.0f}%"


def fmt_temp(value) -> str:
    return "—" if value is None else f"{value:.0f}°C"


class SessionsList(CanvasList):
    """Історія останніх сесій: гра, дата, тривалість, середнє навантаження."""

    wheel_step_dp = _SESSION_ROW_DP * 2

    def __init__(self, master):
        super().__init__(master, bg=theme.BG_PANEL, scrollbar_gap=4)
        self.items: list[dict] = []
        self._hover_fill = theme.lerp_color(self, theme.BG_PANEL_LIGHT, "#ffffff", 0.05)

    def set_items(self, items: list[dict]) -> None:
        self.items = items
        self.set_count(len(items))

    def row_height_dp(self, index: int) -> float:
        return _SESSION_ROW_DP

    def create_slot(self, slot) -> None:
        c = self.canvas
        base = (slot.tag, slot.base_tag)
        it = slot.items
        it["bg"] = c.create_image(0, 0, anchor="nw", tags=base)
        it["name"] = c.create_text(0, 0, anchor="sw", font=self.font(13, "bold"), fill=theme.TEXT_MAIN, tags=base)
        it["sub"] = c.create_text(0, 0, anchor="nw", font=self.font(11), fill=theme.TEXT_DIM, tags=base)
        it["load"] = c.create_text(0, 0, anchor="e", font=self.font(12), fill=theme.TEXT_MAIN, tags=base)

    def bind_slot(self, slot, index: int) -> None:
        item = self.items[index]
        w, mid = self.width, self.px(_SESSION_CARD_DP) // 2
        load = f"CPU {fmt_pct(item.get('cpu_avg'))}  ·  GPU {fmt_pct(item.get('gpu_avg'))}"
        load_w = self.text_width(load, self.font(12))
        tx = self.px(12)
        self.icoords(slot, "bg", 0, 0)
        self.icoords(slot, "name", tx, mid + self.px(1))
        self.iset(slot, "name", text=self.truncate(item["game"], max(w - load_w - self.px(40), self.px(40)),
                                                   self.font(13, "bold")))
        self.icoords(slot, "sub", tx, mid + self.px(2))
        when = time.strftime("%d.%m %H:%M", time.localtime(item["started"]))
        self.iset(slot, "sub", text=f"{when}  ·  {game_sessions.format_duration(item['duration_s'])}")
        self.icoords(slot, "load", w - self.px(12), mid)
        self.iset(slot, "load", text=load)

    def hover_slot(self, slot, index: int, region) -> None:
        self.iset(slot, "bg", image=card_image(
            max(self.width, 20), self.px(_SESSION_CARD_DP), self.px(10),
            self._hover_fill if region else theme.BG_PANEL_LIGHT, theme.BORDER, theme.BG_PANEL,
        ))

    def hit_test(self, index: int, x: int, y: int):
        return "row" if y < self.px(_SESSION_CARD_DP) else None

    def row_identity(self, index: int):
        return self.items[index]["started"]

    def tooltip_for(self, index: int, region: str):
        s = self.items[index]
        return (
            t("game_widgets.session_tip", game=s['game'], duration=game_sessions.format_duration(s['duration_s']), cpu_avg=fmt_pct(s.get('cpu_avg')), cpu_max=fmt_pct(s.get('cpu_max')), gpu_avg=fmt_pct(s.get('gpu_avg')), gpu_max=fmt_pct(s.get('gpu_max')), cpu_temp=fmt_temp(s.get('cpu_temp_max')), gpu_temp=fmt_temp(s.get('gpu_temp_max')))
        )


# =========================================================== сторінка

class ScrollPage(ctk.CTkFrame):
    """Вертикально прокручувана сторінка для кількох карток: canvas + одне вікно
    (create_window) із вмістом `self.inner`. Колесо миші прокручує сторінку,
    якщо курсор не над віртуалізованим списком (той прокручується сам).

    fill_height=True — вміст розтягується на всю висоту, поки вміщується (ваги
    рядків grid працюють як без прокрутки), а на невисокому вікні прокручується;
    смуга прокрутки тоді видна лише при переповненні. Зміна потрібної висоти
    вмісту подій не дає — власник викликає fit_height() після змін розкладки."""

    def __init__(self, master, fill_height: bool = False):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self._fill = fill_height
        self._fill_h = 0
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        S = self._get_widget_scaling()
        self.canvas = tk.Canvas(self, bg=theme.BG_MAIN, highlightthickness=0, bd=0, takefocus=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = FastScrollbar(self, self._on_scrollbar, bg=theme.BG_MAIN, scale=S)
        self.scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 2))
        self.inner = ctk.CTkFrame(self.canvas, fg_color=theme.BG_MAIN, corner_radius=0)
        self._window = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.canvas.configure(yscrollcommand=lambda a, b: self.scrollbar.set(float(a), float(b)))
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.controller = scroll.ScrollController(self, self.canvas)
        scroll.register(self, self.controller, self._overflow, S)

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self.fit_height()

    def _on_inner_configure(self, _event) -> None:
        if self._fill:
            self.fit_height()
        else:
            self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), self.inner.winfo_reqheight()))

    def fit_height(self) -> None:
        """Режим fill_height: висота вмісту = max(висота вікна, потрібна висота)."""
        if not self._fill:
            return
        view_h = self.canvas.winfo_height()
        need = self.inner.winfo_reqheight()
        height = max(view_h, need)
        if height != self._fill_h:
            self._fill_h = height
            self.canvas.itemconfigure(self._window, height=height)
            self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), height))
            if height <= view_h:
                self.canvas.yview_moveto(0)
        if need > view_h > 1:
            self.scrollbar.grid()
        else:
            self.scrollbar.grid_remove()

    def _overflow(self) -> bool:
        return self.inner.winfo_reqheight() > self.canvas.winfo_height()

    def _on_scrollbar(self, kind: str, value, *_rest) -> None:
        if kind == "moveto":
            self.controller.moveto(float(value))
        else:
            self.controller.by_pixels(int(value) * 3 * 48)

    def scroll_to_top(self) -> None:
        self.controller.moveto_now(0.0)
