"""Віджети вкладки «Ігровий режим»: робот у навушниках, великий перемикач,
чіпи програм, списки ігор і сесій та вертикально прокручувана сторінка.

Як і на «Моніторі», нічого «важкого»: робот і перемикач — спрайти Pillow, чіпи —
один Canvas, списки — CanvasList (віртуалізовані), а не сотні CTk-віджетів.
"""

from __future__ import annotations

import random
import time
import tkinter as tk
import tkinter.font as tkfont

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFilter, ImageTk

from core import game_sessions
from core.app_icons import IconLoader
from ui import theme
from ui.widgets import aa
from ui.widgets.canvas_list import (
    CanvasList, FastScrollbar, Tooltip, card_image, switch_image,
)

_FONT_FAMILY = "Segoe UI"


def fmt_mem(mb: float) -> str:
    return f"{mb / 1024:.1f} ГБ" if mb >= 1024 else f"{mb:.0f} МБ"


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
        try:
            self._owner.after(0, self._on_update)
        except (RuntimeError, tk.TclError):
            pass


# ================================================================= робот

ROBOT_UNIT = 1.75  # 88 dp «сітки» робота з Монітора -> ~154 dp на екрані
_BAND = "#3b4b70"


class GameRobot(CanvasBox):
    """Великий робот. Вимкнено — спокійний, очі-риски. Увімкнено — у навушниках із
    мікрофоном, очі світяться (свічення «дихає» — 4 фази, кожна — готовий спрайт)."""

    def __init__(self, master, bg: str = theme.BG_PANEL):
        size = 88 * ROBOT_UNIT
        super().__init__(master, size, size, bg)
        self._sprites: dict = {}
        self._image = self.canvas.create_image(0, 0, anchor="nw")
        self._active = False
        self._blink = False
        self._phase = 0
        self._running = False
        self._after_id = None
        self._t0 = time.perf_counter()
        self._blink_timer = random.uniform(1.6, 3.0)
        self._last_tick = None
        self._show()
        self.bind("<Destroy>", self._on_destroy)

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self._show()

    def set_running(self, running: bool) -> None:
        """Кліпання й «дихання» очей — лише поки вкладку видно."""
        if running and self._after_id is None:
            self._last_tick = None
            self._tick()
        elif not running and self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None

    def on_scale_changed(self) -> None:
        self._sprites.clear()
        self._show()

    def _render(self, active: bool, blink: bool, phase: int) -> ImageTk.PhotoImage:
        u = ROBOT_UNIT
        S = self.S * u
        px = round(88 * S)
        green, blue = aa.rgb(theme.ACCENT_GREEN), aa.rgb(theme.ACCENT_BLUE)
        img = aa.new_layer(px, px, "RGB", aa.rgb(self.bg))
        p = aa.Painter(img, S)

        if active:  # навушники: дуга над головою й чашки на вухах
            p.arc(4, 7, 84, 87, start=8, extent=164, fill=_BAND, width=6, round_caps=True)
            p.arc(7, 10, 81, 84, start=14, extent=152, fill=green, width=1.6, round_caps=True)
        p.line([(44, 10), (44, 18)], fill=aa.rgb(theme.TEXT_DIM), width=2, round_caps=False)
        p.ellipse(40, 2, 48, 10, fill=green if active else aa.rgb(theme.ACCENT_BLUE_DIM))
        p.ellipse(10, 16, 78, 78, fill=blue, outline=green if active else aa.rgb(theme.ACCENT_BLUE_DIM), width=2)
        p.rect(22, 32, 66, 64, fill=aa.rgb(theme.BG_MAIN))

        if active:
            # свічення очей: розмите коло під очима, «дихає» між фазами
            k = aa.SS * S
            glow = Image.new("L", img.size, 0)
            gd = ImageDraw.Draw(glow)
            strength = (150, 190, 225, 190)[phase]
            for cx in (35, 53):
                r = 9.5 * k
                gd.ellipse((cx * k - r, 45 * k - r, cx * k + r, 45 * k + r), fill=strength)
            glow = glow.filter(ImageFilter.GaussianBlur(radius=4.2 * k))
            img.paste(Image.new("RGB", img.size, green), (0, 0), glow)
            p = aa.Painter(img, S)
            if blink:
                p.line([(29, 45), (41, 45)], fill=green, width=2.4)
                p.line([(47, 45), (59, 45)], fill=green, width=2.4)
            else:
                for cx in (35, 53):
                    p.ellipse(cx - 6, 38, cx + 6, 52, fill=green)
                    p.ellipse(cx - 3.2, 40, cx + 0.6, 44, fill=aa.rgb("#d8fff0"))
            p.arc(30, 46, 58, 64, start=200, extent=140, fill=green, width=2)
            # ліва чашка з мікрофоном і права чашка
            p.line([(8, 58), (10, 70), (21, 75)], fill=_BAND, width=2.2)
            p.ellipse(19, 72, 25, 78, fill=green)
            for x0 in (1, 72):
                p.ellipse(x0, 32, x0 + 15, 64, fill=_BAND, outline=green, width=1.6)
        else:
            eye = aa.rgb(theme.TEXT_DIM)
            p.line([(29, 46), (41, 46)], fill=eye, width=2)
            p.line([(47, 46), (59, 46)], fill=eye, width=2)
            p.line([(36, 57), (52, 57)], fill=eye, width=2)
        return ImageTk.PhotoImage(aa.downscale(img, (px, px)))

    def _show(self) -> None:
        key = (self._active, self._blink, self._phase if self._active else 0)
        photo = self._sprites.get(key)
        if photo is None:
            photo = self._sprites[key] = self._render(*key)
        self.canvas.itemconfigure(self._image, image=photo)

    def _tick(self) -> None:
        if not self.winfo_exists():
            return
        if theme.robot_animation_enabled():
            now = time.perf_counter()
            dt = 0.0 if self._last_tick is None else now - self._last_tick
            self._last_tick = now
            changed = False
            self._blink_timer -= dt
            if self._blink_timer <= 0:
                self._blink = not self._blink
                self._blink_timer = random.uniform(0.12, 0.2) if self._blink else random.uniform(2.0, 4.0)
                changed = True
            phase = int((now - self._t0) / 0.45) % 4
            if self._active and phase != self._phase:
                self._phase = phase
                changed = True
            if changed:
                self._show()
        self._after_id = self.after(150, self._tick)

    def _on_destroy(self, event) -> None:
        if event.widget is self and self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None


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
            self._fonts[key] = (_FONT_FAMILY, -round(size * self.S), weight)
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
                    "key": None, "title": f"ще {hidden}", "memory_mb": 0, "icon": False, "removable": False,
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
            return "Ще закриється:\n" + "\n".join(chip["hidden_titles"])
        if region == "x":
            return "Не закривати цю програму (можна повернути кнопкою «Повернути вилучені»)"
        lines = [chip["title"]]
        meta = " · ".join(filter(None, [
            chip.get("category_label"),
            f"процесів: {chip['count']}" if chip.get("count") else None,
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
_SW_W, _SW_H = 40, 22


class GamesList(CanvasList):
    """Знайдені ігри: іконка, назва, платформа, перемикач «вмикати режим автоматично»."""

    wheel_step_dp = _GAME_ROW_DP * 2
    clickable_regions = frozenset({"switch"})

    def __init__(self, master, on_toggle):
        super().__init__(master, bg=theme.BG_PANEL, scrollbar_gap=4)
        self._on_toggle = on_toggle
        self.items: list[dict] = []  # key, name, platform, exe, auto, running
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
        return _GAME_ROW_DP

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
        it["auto"] = c.create_text(0, 0, anchor="e", font=self.font(11), fill=theme.TEXT_DIM, text="Авто", tags=base)

    def _card_box(self):
        return self.px(_GAME_CARD_DP)

    def bind_slot(self, slot, index: int) -> None:
        item = self.items[index]
        w, card = self.width, self.px(_GAME_CARD_DP)
        mid = card // 2
        sw_w, sw_h = self.px(_SW_W), self.px(_SW_H)
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
            self.iset(slot, "run", text="● запущено", state="normal")
        else:
            self.iset(slot, "run", state="hidden")
        self.icoords(slot, "switch", w - self.px(10), mid)
        self.icoords(slot, "auto", w - self.px(10 + _SW_W + 8), mid)
        self.iset(slot, "auto", fill=theme.ACCENT_GREEN if item["auto"] else theme.TEXT_DIM)

    def hover_slot(self, slot, index: int, region) -> None:
        item = self.items[index]
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
        if y >= self.px(_GAME_CARD_DP):
            return None  # проміжок між картками
        x0, y0, x1, y1 = self._switch_box()
        return "switch" if x0 <= x < x1 and y0 <= y < y1 else "row"

    def click(self, index: int, region: str) -> None:
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
        if region == "switch":
            return "Вмикати «Ігровий режим» автоматично, коли ця гра запуститься, і вимикати після виходу"
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
            f"{s['game']} — {game_sessions.format_duration(s['duration_s'])}\n"
            f"CPU: середнє {fmt_pct(s.get('cpu_avg'))}, максимум {fmt_pct(s.get('cpu_max'))}\n"
            f"GPU: середнє {fmt_pct(s.get('gpu_avg'))}, максимум {fmt_pct(s.get('gpu_max'))}\n"
            f"Макс. температура: CPU {fmt_temp(s.get('cpu_temp_max'))}, GPU {fmt_temp(s.get('gpu_temp_max'))}"
        )


# =========================================================== сторінка

class ScrollPage(ctk.CTkFrame):
    """Вертикально прокручувана сторінка для кількох карток: canvas + одне вікно
    (create_window) із вмістом `self.inner`. Колесо миші прокручує сторінку,
    якщо курсор не над віртуалізованим списком (той прокручується сам)."""

    def __init__(self, master):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        S = self._get_widget_scaling()
        self.canvas = tk.Canvas(self, bg=theme.BG_MAIN, highlightthickness=0, bd=0, takefocus=0,
                                yscrollincrement=round(48 * S))
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = FastScrollbar(self, self._on_scrollbar, bg=theme.BG_MAIN, scale=S)
        self.scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 2))
        self.inner = ctk.CTkFrame(self.canvas, fg_color="transparent", corner_radius=0)
        self._window = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.canvas.configure(yscrollcommand=lambda a, b: self.scrollbar.set(float(a), float(b)))
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.inner.bind("<Configure>", self._on_inner_configure)
        tk.Misc.bind_all(self, "<MouseWheel>", self._on_wheel, "+")  # CTk забороняє bind_all у своїх віджетах

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _on_inner_configure(self, _event) -> None:
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), self.inner.winfo_reqheight()))

    def _overflow(self) -> bool:
        return self.inner.winfo_reqheight() > self.canvas.winfo_height()

    def _on_scrollbar(self, kind: str, value, *_rest) -> None:
        if kind == "moveto":
            self.canvas.yview_moveto(value)
        else:
            self.canvas.yview_scroll(int(value) * 3, "units")

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0)

    def _on_wheel(self, event) -> None:
        if not self.winfo_ismapped() or not self._overflow():
            return
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget is self:
                break
            if isinstance(widget, (CanvasList, tk.Listbox)):
                return  # список прокручується сам
            widget = getattr(widget, "master", None)
        else:
            return
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        theme.notify_scroll()
