"""Швидкі віртуалізовані списки, намальовані на одному tk.Canvas.

Чому не CTk-віджети в рядках: кожен CTkLabel/CTkButton/CTkFrame — окреме
вікно Windows зі своїм canvas, яке CTk перебудовує при кожній зміні кольору
чи тексту (заокруглені фігури рахуються в Python). Сотні таких віджетів
гальмують прокрутку й наведення, а при швидкому зсуві Windows не встигає
їх перемалювати — рядки "двоїлися" й накладалися.

Тут увесь список — один Canvas:
  * рядок = група canvas-елементів (текст, зображення, лінії) під спільним
    тегом; пул рядків фіксований — стільки, скільки влазить на екран + 2;
  * прокрутка лише зсуває групи (canvas.move) і перезаповнює дані в тих
    рядках, що з'явилися з-за краю — жодного створення/знищення елементів;
  * наведення й кліки визначаються за координатами (hit_test), колір
    змінюється миттєво підміною зображення — без анімацій;
  * фон карток, чекбокси, перемикачі й кнопки — згладжені (Pillow 4x)
    зображення, що рендеряться один раз і кешуються (PhotoImage).

Підклас реалізує create_slot / bind_slot / hover_slot / hit_test і, за
потреби, click / double_click / tooltip_for / row_height_dp.
"""

from __future__ import annotations

import bisect
import tkinter as tk
import tkinter.font as tkfont

from PIL import Image, ImageDraw, ImageTk

from core import process_info
from ui import theme
from ui.widgets import aa

_FONT_FAMILY = "Segoe UI"
_WHEEL_EASE = 0.35  # частка залишку прокрутки, що проходиться за кадр


# =============================================================== зображення

_image_cache: dict = {}


def _cached(key, build):
    photo = _image_cache.get(key)
    if photo is None:
        if len(_image_cache) > 400:  # напр. після багатьох змін ширини вікна
            _image_cache.clear()
        photo = _image_cache[key] = ImageTk.PhotoImage(build())
    return photo


def _rounded(w: int, h: int, radius: float, fill, border=None, border_w: float = 0, bg=None) -> Image.Image:
    """Згладжений заокруглений прямокутник w x h px (RGBA, якщо bg=None)."""
    K = aa.SS
    mode, base = ("RGBA", (0, 0, 0, 0)) if bg is None else ("RGB", aa.rgb(bg))
    layer = Image.new(mode, (w * K, h * K), base)
    d = ImageDraw.Draw(layer)
    fill_c = aa.rgb(fill, 255) if mode == "RGBA" else aa.rgb(fill)
    if border and border_w > 0:
        border_c = aa.rgb(border, 255) if mode == "RGBA" else aa.rgb(border)
        d.rounded_rectangle((0, 0, w * K - 1, h * K - 1), radius=radius * K, fill=border_c)
        inset = border_w * K
        d.rounded_rectangle(
            (inset, inset, w * K - 1 - inset, h * K - 1 - inset),
            radius=max(0.0, radius * K - inset), fill=fill_c,
        )
    else:
        d.rounded_rectangle((0, 0, w * K - 1, h * K - 1), radius=radius * K, fill=fill_c)
    return aa.downscale(layer, (w, h))


def card_image(w: int, h: int, radius: int, fill: str, border: str, bg: str, border_w: int = 1):
    """Фон картки будь-якого розміру за 9-slice: згладжені кути рендеряться
    на маленькому зображенні, а краї й середина лише розтягуються — тому
    навіть широка картка готова за частки мілісекунди."""
    def build():
        c = radius + 2  # розмір кута в px
        k = 2 * c + 1
        small = _rounded(k, k, radius, fill, border, border_w, bg)
        out = Image.new("RGB", (w, h), aa.rgb(fill))
        mid_w, mid_h = max(w - 2 * c, 0), max(h - 2 * c, 0)
        # кути
        out.paste(small.crop((0, 0, c, c)), (0, 0))
        out.paste(small.crop((k - c, 0, k, c)), (w - c, 0))
        out.paste(small.crop((0, k - c, c, k)), (0, h - c))
        out.paste(small.crop((k - c, k - c, k, k)), (w - c, h - c))
        # краї — розтягнута центральна смужка маленького зображення
        if mid_w:
            out.paste(small.crop((c, 0, c + 1, c)).resize((mid_w, c)), (c, 0))
            out.paste(small.crop((c, k - c, c + 1, k)).resize((mid_w, c)), (c, h - c))
        if mid_h:
            out.paste(small.crop((0, c, c, c + 1)).resize((c, mid_h)), (0, c))
            out.paste(small.crop((k - c, c, k, c + 1)).resize((c, mid_h)), (w - c, c))
        return out
    return _cached(("card", w, h, radius, fill, border, bg, border_w), build)


def pill_image(w: int, h: int, fill: str, radius: float | None = None):
    """Заливна заокруглена "пігулка" (кнопки, бейджі) з прозорим тлом."""
    r = h / 2 if radius is None else radius
    return _cached(("pill", w, h, fill, r), lambda: _rounded(w, h, r, fill))


def checkbox_image(size: int, checked: bool, hover: bool, disabled: bool, scale: float):
    """Чекбокс у стилі PulseFPS (як CTkCheckBox: рамка 2 dp, радіус 6 dp)."""
    def build():
        K = aa.SS
        layer = Image.new("RGBA", (size * K, size * K), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        r = 6 * scale * K
        box = (0, 0, size * K - 1, size * K - 1)
        if checked:
            color = theme.BORDER if disabled else (theme.ACCENT_GREEN_DIM if hover else theme.ACCENT_GREEN)
            d.rounded_rectangle(box, radius=r, fill=aa.rgb(color, 255))
            s = size * K
            pts = [(s * .26, s * .52), (s * .43, s * .69), (s * .75, s * .33)]
            d.line(pts, fill=aa.rgb(theme.BG_MAIN, 255), width=round(2.2 * scale * K), joint="curve")
        else:
            color = theme.BORDER if disabled else (theme.TEXT_MAIN if hover else theme.TEXT_DIM)
            bw = 2 * scale * K
            d.rounded_rectangle(box, radius=r, fill=aa.rgb(color, 255))
            d.rounded_rectangle(
                (bw, bw, size * K - 1 - bw, size * K - 1 - bw), radius=max(0, r - bw), fill=(0, 0, 0, 0),
            )
        return aa.downscale(layer, (size, size))
    return _cached(("check", size, checked, hover, disabled, round(scale, 3)), build)


def switch_image(w: int, h: int, on: bool, hover: bool, disabled: bool):
    """Перемикач (як CTkSwitch): доріжка-пігулка й кругла ручка."""
    def build():
        K = aa.SS
        layer = Image.new("RGBA", (w * K, h * K), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        if disabled:
            track = theme.BORDER
        elif on:
            track = theme.ACCENT_GREEN_DIM if hover else theme.ACCENT_GREEN
        else:
            track = "#3a4560" if hover else "#2d3953"
        d.rounded_rectangle((0, 0, w * K - 1, h * K - 1), radius=h * K / 2, fill=aa.rgb(track, 255))
        pad = h * K * 0.16
        knob = h * K - 2 * pad
        x0 = w * K - pad - knob if on else pad
        knob_color = theme.TEXT_DIM if disabled else "#ffffff"
        d.ellipse((x0, pad, x0 + knob, pad + knob), fill=aa.rgb(knob_color, 255))
        return aa.downscale(layer, (w, h))
    return _cached(("switch", w, h, on, hover, disabled), build)


# ================================================== позначки процесів

# вид процесу (core/process_info.py) -> (текст позначки, колір)
_BADGE_COLORS = {
    process_info.SYSTEM: theme.TEXT_DIM,
    process_info.ANTICHEAT: theme.WARNING,
    process_info.SAFE: theme.ACCENT_GREEN,
}
PROCESS_BADGES = {kind: (text, _BADGE_COLORS[kind]) for kind, text in process_info.BADGES.items()}


# ================================================================ скролбар

class FastScrollbar(tk.Canvas):
    """Тонкий скролбар: повзунок — одна лінія з круглими кінцями. set() лише
    зсуває її координати (жодної перебудови фігур, як у CTkScrollbar)."""

    def __init__(self, master, command, *, bg: str, scale: float, width_dp: int = 10):
        self._scale = scale
        super().__init__(
            master, width=round(width_dp * scale), bg=bg, highlightthickness=0, bd=0,
        )
        self._command = command
        self._first, self._last = 0.0, 1.0
        self._drag_from = None
        self._hover = False
        self._thumb = self.create_line(0, 0, 0, 0, fill=theme.BORDER, capstyle="round", state="hidden")
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))

    def set(self, first: float, last: float) -> None:
        if (first, last) != (self._first, self._last):
            self._first, self._last = first, last
            self._redraw()

    def _geometry(self):
        w, h = self.winfo_width(), self.winfo_height()
        thick = max(4, round(6 * self._scale))
        pad = thick / 2 + 2
        track = max(h - 2 * pad, 1)
        min_len = 28 * self._scale
        length = max((self._last - self._first) * track, min_len)
        start = pad + self._first * (track - length) / max(1 - (self._last - self._first), 1e-6)
        return w, thick, start, start + length

    def _redraw(self) -> None:
        if self._last - self._first >= 0.999:
            self.itemconfigure(self._thumb, state="hidden")
            return
        w, thick, y0, y1 = self._geometry()
        self.coords(self._thumb, w / 2, y0, w / 2, y1)
        self.itemconfigure(self._thumb, width=thick, state="normal")

    def _set_hover(self, hover: bool) -> None:
        if hover != self._hover:
            self._hover = hover
            self.itemconfigure(self._thumb, fill=theme.TEXT_DIM if hover or self._drag_from else theme.BORDER)

    def _on_press(self, event) -> None:
        if self._last - self._first >= 0.999:
            return
        _w, _t, y0, y1 = self._geometry()
        if y0 <= event.y <= y1:
            self._drag_from = (event.y, self._first)
        else:
            self._command("page", -1 if event.y < y0 else 1)

    def _on_drag(self, event) -> None:
        if self._drag_from is None:
            return
        _w, _t, y0, y1 = self._geometry()
        track = self.winfo_height() - (y1 - y0) - 2 * (_t / 2 + 2)
        span = 1 - (self._last - self._first)
        if track <= 0 or span <= 0:
            return
        y_start, first = self._drag_from
        new_first = first + (event.y - y_start) / track * span
        self._command("moveto", max(0.0, min(new_first / span, 1.0)))

    def _on_release(self, _event) -> None:
        self._drag_from = None
        self.itemconfigure(self._thumb, fill=theme.TEXT_DIM if self._hover else theme.BORDER)


# ================================================================= підказка

class Tooltip:
    """Одна підказка на список: з'являється після паузи, зникає при русі/прокрутці."""

    DELAY_MS = 350

    def __init__(self, owner: tk.Misc):
        self._owner = owner
        self._win: tk.Toplevel | None = None
        self._job = None

    def schedule(self, text: str) -> None:
        self.hide()
        self._job = self._owner.after(self.DELAY_MS, lambda: self._show(text))

    def _show(self, text: str) -> None:
        self._job = None
        try:
            x, y = self._owner.winfo_pointerxy()
        except tk.TclError:
            return
        win = tk.Toplevel(self._owner)
        win.wm_overrideredirect(True)
        win.wm_geometry(f"+{x + 12}+{y + 18}")
        win.attributes("-topmost", True)  # поверх головного вікна, навіть якщо воно «завжди зверху»
        tk.Label(
            win, text=text, background="#1a1a1a", foreground="#dce4ee", font=(_FONT_FAMILY, 10),
            padx=8, pady=4, relief="solid", borderwidth=1, wraplength=380, justify="left",
        ).pack()
        self._win = win

    def hide(self) -> None:
        if self._job is not None:
            try:
                self._owner.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if self._win is not None:
            try:
                self._win.destroy()
            except tk.TclError:
                pass
            self._win = None


# ==================================================================== слот

class Slot:
    """Один рядок пулу: набір canvas-елементів під тегом `tag`.

    Елементи створюються з координатами відносно початку рядка (0, 0);
    `y` — поточний зсув групи на canvas. `items` — довільні id елементів,
    `data` — будь-що, що рендереру треба пам'ятати між bind/hover."""

    __slots__ = ("tag", "base_tag", "y", "index", "items", "data", "_cache")

    def __init__(self, n: int):
        self.tag = f"slot{n}"
        self.base_tag = f"slot{n}base"  # завжди видимі елементи рядка
        self.y = 0
        self.index: int | None = None
        self.items: dict = {}
        self.data: dict = {}
        self._cache: dict = {}


# ================================================================== список

class CanvasList(theme.PlainFrame):
    """База віртуалізованого списку на Canvas. Висоти рядків — у dp
    (row_height_dp), позиції рахуються наперед, видимий діапазон — бінарним
    пошуком, тож 10 чи 10 000 рядків коштують однаково."""

    wheel_step_dp = 80
    clickable_regions: frozenset = frozenset()
    sound_regions: frozenset = frozenset()

    def __init__(self, master, *, bg: str = theme.BG_MAIN, scrollbar_gap: int = 6):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.S = self._get_widget_scaling()
        self.bg = bg
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, takefocus=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = FastScrollbar(self, self._on_scrollbar, bg=bg, scale=self.S)
        self.scrollbar.grid(row=0, column=1, sticky="ns", padx=(round(scrollbar_gap * self.S), 0))
        self.tooltip = Tooltip(self)

        self.count = 0
        self._tops: list[int] = [0]  # px, tops[i] — верх рядка i; tops[count] — загальна висота
        self._slots: dict[int, Slot] = {}
        self._free: list[Slot] = []
        self._slot_counter = 0
        self._offset = 0.0
        self._target = 0.0
        self._scrolling = False
        self.width = 0
        self._height = 0
        self._hover: tuple = (None, None)
        self._tip_identity = None
        self._pressed: tuple | None = None
        self._layout_job = None
        self._fonts: dict = {}
        self._measure: dict = {}
        self._trunc_cache: dict = {}

        self._empty = self.canvas.create_text(
            0, 0, text="", fill=theme.TEXT_DIM, font=self.font(13), state="hidden",
        )

        c = self.canvas
        c.bind("<Configure>", self._on_configure)
        c.bind("<Motion>", self._on_motion)
        c.bind("<Enter>", self._on_motion)  # курсор міг зайти без руху (напр. після прокрутки сторінки)
        c.bind("<Leave>", self._on_leave)
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Double-Button-1>", self._on_double)
        for widget in (c, self.scrollbar):
            widget.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Destroy>", lambda e: self.tooltip.hide() if e.widget is self else None)

    # ------------------------------------------------------------ хуки

    def create_slot(self, slot: Slot) -> None:
        """Створити елементи рядка (тег slot.tag; завжди видимі — ще й slot.base_tag)."""
        raise NotImplementedError

    def bind_slot(self, slot: Slot, index: int) -> None:
        """Заповнити рядок даними index (координати відносно (0, 0), ширина self.width)."""
        raise NotImplementedError

    def hover_slot(self, slot: Slot, index: int, region: str | None) -> None:
        """Стан наведення: region=None — курсор не над рядком."""

    def hit_test(self, index: int, x: int, y: int) -> str | None:
        """Ділянка рядка під точкою (px відносно рядка); None — проміжок між рядками."""
        return "row"

    def click(self, index: int, region: str) -> None:
        pass

    def double_click(self, index: int, region: str) -> None:
        pass

    def tooltip_for(self, index: int, region: str) -> str | None:
        return None

    def row_identity(self, index: int):
        """Що саме зараз у рядку index (напр. PID). Якщо при оновленні даних під
        курсором опинився інший елемент — підказка перебудовується."""
        return index

    def row_height_dp(self, index: int) -> float:
        raise NotImplementedError

    def on_scale_changed(self) -> None:
        """DPI змінився — скинути кеші, що залежать від масштабу."""

    # --------------------------------------------------------- утиліти

    def px(self, dp: float) -> int:
        return round(dp * self.S)

    def font(self, size: int, weight: str = "normal") -> tuple:
        key = (size, weight)
        f = self._fonts.get(key)
        if f is None:
            f = self._fonts[key] = (_FONT_FAMILY, -round(size * self.S), weight)
        return f

    def text_width(self, text: str, font: tuple) -> int:
        m = self._measure.get(font)
        if m is None:
            m = self._measure[font] = tkfont.Font(family=font[0], size=font[1], weight=font[2])
        return m.measure(text)

    def truncate(self, text: str, max_px: int, font: tuple) -> str:
        """Обрізає текст із "…" рівно під max_px (кешується)."""
        key = (text, max_px, font)
        cached = self._trunc_cache.get(key)
        if cached is not None:
            return cached
        if self.text_width(text, font) <= max_px:
            result = text
        else:
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self.text_width(text[:mid].rstrip() + "…", font) <= max_px:
                    lo = mid
                else:
                    hi = mid - 1
            result = text[:lo].rstrip() + "…" if lo else "…"
        if len(self._trunc_cache) > 5000:
            self._trunc_cache.clear()
        self._trunc_cache[key] = result
        return result

    def badge_image(self, text: str, color: str):
        """(PhotoImage, ширина px) пігулки-позначки під текст шрифтом font(10, "bold"):
        тло — колір позначки, ледь підмішаний до фону списку."""
        w = self.text_width(text, self.font(10, "bold")) + self.px(12)
        fill = theme.lerp_color(self, self.bg, color, 0.2)
        return pill_image(w, self.px(18), fill), w

    def iset(self, slot: Slot, name: str, **options) -> None:
        """itemconfigure елемента slot.items[name], лише якщо опції змінилися —
        оновлення щосекунди (Монітор) не смикають Tk дарма."""
        key = tuple(sorted(options.items()))
        if slot._cache.get(name) != key:
            slot._cache[name] = key
            self.canvas.itemconfigure(slot.items[name], **options)

    def icoords(self, slot: Slot, name: str, *coords) -> None:
        key = ("coords",) + coords
        if slot._cache.get(("c", name)) != key:
            slot._cache[("c", name)] = key
            self.canvas.coords(slot.items[name], *coords)

    def slot_for(self, index: int) -> Slot | None:
        return self._slots.get(index)

    @property
    def hover_index(self) -> int | None:
        return self._hover[0]

    # ------------------------------------------------------------ дані

    def set_count(self, count: int, *, keep_scroll: bool = False) -> None:
        """Нова кількість рядків (дані змінилися): перерахувати висоти й
        перезаповнити видимі рядки."""
        self.count = count
        if not keep_scroll:
            self._offset = self._target = 0.0
        self._measure_rows()
        self._release_all()
        self.tooltip.hide()
        self._layout()
        self._update_hover_from_pointer()

    def refresh(self) -> None:
        """Перезаповнити всі видимі рядки (змінилися дані, але не кількість)."""
        self._release_all()
        self._layout()
        self._update_hover_from_pointer()

    def update_visible(self) -> None:
        """М'яке оновлення видимих рядків на місці (часті оновлення даних):
        bind_slot викликається знову, а iset()/icoords() пропускають усе, що не
        змінилося, — тож Tk отримує лише реальні зміни."""
        c = self.canvas
        for idx, slot in self._slots.items():
            if slot.y:
                c.move(slot.tag, 0, -slot.y)
            self.bind_slot(slot, idx)
            self.hover_slot(slot, idx, self._hover[1] if self._hover[0] == idx else None)
            if slot.y:
                c.move(slot.tag, 0, slot.y)
        hover_idx = self._hover[0]
        if hover_idx is not None and self.row_identity(hover_idx) != self._tip_identity:
            self._schedule_tooltip()  # під курсором тепер інший рядок (дані пересортувались)

    def remeasure(self) -> None:
        """Висоти рядків могли змінитися (напр. інший текст із переносом)."""
        self._measure_rows()
        self.refresh()

    def refresh_index(self, index: int) -> None:
        slot = self._slots.get(index)
        if slot is not None:
            self._bind_slot(slot, index)

    def set_empty_text(self, text: str) -> None:
        self.canvas.itemconfigure(self._empty, text=text)
        self._place_empty()

    def _place_empty(self) -> None:
        state = "normal" if self.count == 0 and self.canvas.itemcget(self._empty, "text") else "hidden"
        self.canvas.coords(self._empty, self.width / 2, max(self._height * 0.35, self.px(30)))
        self.canvas.itemconfigure(self._empty, state=state)

    def _measure_rows(self) -> None:
        tops = [0]
        acc = 0
        S = self.S
        for i in range(self.count):
            acc += round(self.row_height_dp(i) * S)
            tops.append(acc)
        self._tops = tops

    # ---------------------------------------------------------- розкладка

    def _release_all(self) -> None:
        for slot in self._slots.values():
            slot.index = None
            self.canvas.itemconfigure(slot.tag, state="hidden")
            self._free.append(slot)
        self._slots.clear()
        self._hover = (None, None)

    def _new_slot(self) -> Slot:
        slot = Slot(self._slot_counter)
        self._slot_counter += 1
        self.create_slot(slot)
        self.canvas.itemconfigure(slot.tag, state="hidden")
        return slot

    def _bind_slot(self, slot: Slot, index: int) -> None:
        """Повне перезаповнення рядка: група тимчасово повертається в (0, 0),
        щоб рендерер працював у відносних координатах, і назад на своє місце."""
        c = self.canvas
        y = slot.y
        if y:
            c.move(slot.tag, 0, -y)
        slot.index = index
        slot._cache.clear()
        c.itemconfigure(slot.tag, state="hidden")
        c.itemconfigure(slot.base_tag, state="normal")
        self.bind_slot(slot, index)
        self.hover_slot(slot, index, self._hover[1] if self._hover[0] == index else None)
        if y:
            c.move(slot.tag, 0, y)

    def _on_configure(self, event) -> None:
        if (event.width, event.height) == (self.width, self._height):
            return
        width_changed = event.width != self.width
        self.width, self._height = event.width, event.height
        if width_changed:
            # від ширини залежать обрізання назв, перенос тексту й фон карток —
            # перераховуємо один раз, коли зміна розміру вікна "вляжеться"
            if self._layout_job is not None:
                self.after_cancel(self._layout_job)
            self._layout_job = self.after(40, self._on_width_settled)
        self._layout()

    def _on_width_settled(self) -> None:
        self._layout_job = None
        self.on_width_changed()
        self._measure_rows()
        self.refresh()

    def on_width_changed(self) -> None:
        """Підклас може скинути кеші, що залежать від ширини."""

    def _max_offset(self) -> float:
        return max(0.0, self._tops[-1] - self._height)

    def _layout(self) -> None:
        if self._height <= 1 or self.width <= 1:
            return
        self._offset = max(0.0, min(self._offset, self._max_offset()))
        top = round(self._offset)
        bottom = top + self._height
        tops = self._tops
        first = max(0, bisect.bisect_right(tops, top) - 1)
        last = first
        while last < self.count and tops[last] < bottom:
            last += 1

        for idx in [i for i in self._slots if not first <= i < last]:
            slot = self._slots.pop(idx)
            slot.index = None
            self.canvas.itemconfigure(slot.tag, state="hidden")
            self._free.append(slot)

        c = self.canvas
        for idx in range(first, last):
            slot = self._slots.get(idx)
            if slot is None:
                slot = self._free.pop() if self._free else self._new_slot()
                self._slots[idx] = slot
                self._bind_slot(slot, idx)
            y = tops[idx] - top
            if y != slot.y:
                c.move(slot.tag, 0, y - slot.y)
                slot.y = y

        total = tops[-1]
        if total <= self._height or total <= 0:
            self.scrollbar.set(0.0, 1.0)
        else:
            self.scrollbar.set(top / total, min(1.0, bottom / total))
        self._place_empty()

    # ---------------------------------------------------------- прокрутка

    def scroll_to(self, offset_px: float, smooth: bool = False) -> None:
        self._target = max(0.0, min(offset_px, self._max_offset()))
        if smooth and theme.animations_enabled():
            if not self._scrolling:
                self._scrolling = True
                theme.ticker.add(self, self)
        else:
            self._offset = self._target
            self._after_scroll()

    def _on_wheel(self, event) -> None:
        base = self._target if self._scrolling else self._offset
        self.scroll_to(base - (event.delta / 120) * self.px(self.wheel_step_dp), smooth=True)

    def _step(self, _now: float) -> bool:
        """Кадр плавної прокрутки (викликає спільний таймер theme.ticker)."""
        diff = self._target - self._offset
        if abs(diff) < 0.75:
            self._offset = self._target
            self._scrolling = False
        else:
            self._offset += diff * _WHEEL_EASE
        self._after_scroll()
        return self._scrolling

    def _after_scroll(self) -> None:
        theme.notify_scroll()
        self.tooltip.hide()
        self._layout()
        self._update_hover_from_pointer()

    def _on_scrollbar(self, kind: str, value) -> None:
        if kind == "moveto":
            self.scroll_to(value * self._max_offset())
        else:
            self.scroll_to(self._target + value * self._height * 0.9, smooth=True)

    # ------------------------------------------------------------ курсор

    def _locate(self, x: int, y: int) -> tuple:
        if not (0 <= x < self.width and 0 <= y < self._height):
            return None, None
        abs_y = y + round(self._offset)
        idx = bisect.bisect_right(self._tops, abs_y) - 1
        if not 0 <= idx < self.count:
            return None, None
        region = self.hit_test(idx, x, abs_y - self._tops[idx])
        return (idx, region) if region is not None else (None, None)

    def _set_hover(self, idx, region) -> None:
        if (idx, region) == self._hover:
            return
        old_idx, _old_region = self._hover
        self._hover = (idx, region)
        if old_idx is not None and old_idx != idx:
            slot = self._slots.get(old_idx)
            if slot is not None:
                self.hover_slot(slot, old_idx, None)
        if idx is not None:
            slot = self._slots.get(idx)
            if slot is not None:
                self.hover_slot(slot, idx, region)
        self.canvas.configure(cursor="hand2" if region in self.clickable_regions else "")
        self._schedule_tooltip()

    def _schedule_tooltip(self) -> None:
        self.tooltip.hide()
        idx, region = self._hover
        self._tip_identity = None
        if idx is not None and not self._scrolling:
            text = self.tooltip_for(idx, region)
            if text:
                self._tip_identity = self.row_identity(idx)
                self.tooltip.schedule(text)

    def _on_motion(self, event) -> None:
        self._set_hover(*self._locate(event.x, event.y))

    def _on_leave(self, _event) -> None:
        self._set_hover(None, None)

    def _update_hover_from_pointer(self) -> None:
        try:
            px, py = self.canvas.winfo_pointerxy()
            x, y = px - self.canvas.winfo_rootx(), py - self.canvas.winfo_rooty()
        except tk.TclError:
            return
        self._set_hover(*self._locate(x, y))

    def _on_press(self, event) -> None:
        self._pressed = self._locate(event.x, event.y)

    def _on_release(self, event) -> None:
        pressed, self._pressed = self._pressed, None
        hit = self._locate(event.x, event.y)
        if pressed is None or hit != pressed or hit[0] is None:
            return
        if hit[1] in self.sound_regions:
            theme.play_click()
        self.click(*hit)

    def _on_double(self, event) -> None:
        hit = self._locate(event.x, event.y)
        if hit[0] is not None:
            self.double_click(*hit)

    # ------------------------------------------------------------ масштаб

    def _set_scaling(self, *args, **kwargs):
        super()._set_scaling(*args, **kwargs)
        if not hasattr(self, "canvas"):
            return
        self.S = args[0]
        self._fonts.clear()
        self._measure.clear()
        self._trunc_cache.clear()
        self.scrollbar._scale = self.S
        self.scrollbar.configure(width=round(10 * self.S))
        self.canvas.itemconfigure(self._empty, font=self.font(13))
        self.on_scale_changed()
        # елементи рядків створені під старий масштаб — будуємо пул заново
        for slot in list(self._slots.values()) + self._free:
            self.canvas.delete(slot.tag)
        self._slots.clear()
        self._free.clear()
        self._measure_rows()
        self._layout()
