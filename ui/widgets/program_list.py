"""Віртуалізований список карток програм для вкладки «Програми».

Увесь список намальований на одному tk.Canvas (ui/widgets/canvas_list.py):
фіксований пул рядків — стільки, скільки влазить у видиму область + запас;
при прокрутці групи елементів лише зсуваються, а дані перепризначаються
рядкам, що виїхали з-за краю. Фон картки, чекбокс, бейдж і кнопки —
згладжені кешовані зображення; наведення підміняє їх миттєво.

Колонки (dp від правого краю картки): дата/кнопки — 206, розмір — 112,
бейдж магазину — 66; назва займає решту й обрізається з "…" точно під
свою ширину (повна назва — у підказці).
"""

from __future__ import annotations

from datetime import date

from PIL import Image, ImageDraw, ImageFont, ImageTk

from core.app_icons import IconLoader
from core.cleanup import format_size
from ui import theme
from ui.widgets import aa
from ui.widgets.canvas_list import CanvasList, Slot, card_image, checkbox_image, pill_image

ROW_H = 68  # dp: крок рядків у списку
CARD_H = 62  # dp: висота самої картки (решта — проміжок)
ICON_DP = 32
CARD_RADIUS = 10

# ширини колонок (dp)
COL_BADGE, COL_SIZE, COL_RIGHT = 66, 112, 206
_CHECK_X, _CHECK_DP = 14, 20
_ICON_X = 48
_NAME_X = 94

CATEGORIES = (
    ("game", "Ігри", theme.ACCENT_BLUE),
    ("app", "Програми", theme.ACCENT_GREEN),
    ("system", "Системні", "#c77dff"),
    ("other", "Інше", "#5b6680"),
)
CATEGORY_COLORS = {key: color for key, _label, color in CATEGORIES}
STORE_COLORS = {"Steam": "#66c0f4", "Epic": "#dfe6f2", "Riot": theme.ERROR, "Rockstar": theme.WARNING}

_PLACEHOLDER_COLORS = (theme.ACCENT_BLUE, theme.ACCENT_GREEN, "#c77dff", theme.WARNING, "#ff8a5c", "#5cd6c0")
_DELETE_HOVER = "#e04a68"
_OPEN_HOVER = "#2d3953"

# кнопки, що з'являються при наведенні: (ділянка, текст, ширина dp, правий край dp від краю картки)
_BUTTONS = (("delete", "Видалити", 80, 14), ("open", "Відкрити папку", 112, 100))
_BUTTON_H = 28


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


# ----------------------------------------------------------------------- список

class VirtualList(CanvasList):
    """Список програм. Колбеки: on_toggle(program, checked), on_open(program),
    on_uninstall(program). `selected` — спільна з вкладкою множина ключів."""

    wheel_step_dp = ROW_H * 1.2
    clickable_regions = frozenset({"check", "open", "delete"})
    sound_regions = frozenset({"open", "delete"})

    def __init__(self, master, *, selected: set, on_toggle, on_open, on_uninstall):
        super().__init__(master, bg=theme.BG_MAIN)
        self.selected = selected
        self.on_toggle, self.on_open, self.on_uninstall = on_toggle, on_open, on_uninstall
        self.max_size = 0
        self._items: list[dict] = []
        self._photo_cache: dict = {}
        self.icons = IconLoader(self._icon_ready_threadsafe)

    # ------------------------------------------------------------- дані

    def set_items(self, items: list[dict], *, keep_scroll: bool = False, empty_text: str = "") -> None:
        self._items = items
        self.set_empty_text(empty_text)
        self.set_count(len(items), keep_scroll=keep_scroll)

    def refresh_visible(self) -> None:
        """Перезаповнює всі видимі рядки (зміна розмірів, вибору тощо)."""
        self.refresh()

    def refresh_key(self, key: str) -> None:
        for idx, slot in list(self._slots.items()):
            if self._items[idx]["key"] == key:
                self._bind_size(slot, self._items[idx])

    def refresh_selection(self) -> None:
        for idx, slot in list(self._slots.items()):
            self._apply_state(slot, idx)

    def row_height_dp(self, index: int) -> float:
        return ROW_H

    # ------------------------------------------------------------ геометрія

    def _name_px(self) -> int:
        fixed = self.px(_NAME_X + COL_BADGE + COL_SIZE + COL_RIGHT + 8)
        return max(self.width - fixed, self.px(60))

    def _button_boxes(self, program: dict):
        """(ділянка, x0, y0, x1, y1) кнопок при наведенні, px відносно рядка."""
        h = self.px(_BUTTON_H)
        y0 = (self.px(CARD_H) - h) // 2
        for region, _text, w_dp, right_dp in _BUTTONS:
            if region == "open" and not program.get("install_folder"):
                continue
            x1 = self.width - self.px(right_dp)
            yield region, x1 - self.px(w_dp), y0, x1, y0 + h

    # ---------------------------------------------------------- рядки пулу

    def create_slot(self, slot: Slot) -> None:
        c = self.canvas
        base = (slot.tag, slot.base_tag)
        opt = (slot.tag,)
        it = slot.items
        it["bg"] = c.create_image(0, 0, anchor="nw", tags=base)
        it["check"] = c.create_image(0, 0, anchor="nw", tags=base)
        it["icon"] = c.create_image(0, 0, anchor="nw", tags=base)
        it["name"] = c.create_text(0, 0, anchor="sw", fill=theme.TEXT_MAIN, font=self.font(13, "bold"), tags=base)
        it["publisher"] = c.create_text(0, 0, anchor="nw", fill=theme.TEXT_DIM, font=self.font(11), tags=base)
        it["badge_bg"] = c.create_image(0, 0, anchor="center", tags=opt)
        it["badge"] = c.create_text(0, 0, anchor="center", font=self.font(10, "bold"), tags=opt)
        it["size"] = c.create_text(0, 0, anchor="se", fill=theme.TEXT_MAIN, font=self.font(13, "bold"), tags=base)
        width = self.px(4)
        it["bar_track"] = c.create_line(0, 0, 0, 0, width=width, capstyle="round", fill=theme.BORDER, tags=base)
        it["bar"] = c.create_line(0, 0, 0, 0, width=width, capstyle="round", tags=opt)
        it["date"] = c.create_text(0, 0, anchor="e", fill=theme.TEXT_DIM, font=self.font(11), tags=opt)
        for region, text, _w, _r in _BUTTONS:
            it[f"{region}_bg"] = c.create_image(0, 0, anchor="nw", tags=opt)
            it[f"{region}_text"] = c.create_text(
                0, 0, anchor="center", text=text, font=self.font(11),
                fill="#ffffff" if region == "delete" else theme.TEXT_MAIN, tags=opt,
            )

    def bind_slot(self, slot: Slot, index: int) -> None:
        program = self._items[index]
        c = self.canvas
        it = slot.items
        card_h = self.px(CARD_H)
        mid = card_h // 2
        w = self.width

        c.coords(it["check"], self.px(_CHECK_X), mid - self.px(_CHECK_DP) // 2)
        icon_px = self.px(ICON_DP)
        c.coords(it["icon"], self.px(_ICON_X), mid - icon_px // 2)
        c.itemconfigure(it["icon"], image=self.icon_photo(program))

        name_px = self._name_px()
        full_name = program["name"]
        shown = self.truncate(full_name, name_px, self.font(13, "bold"))
        slot.data["name_truncated"] = shown != full_name
        c.coords(it["name"], self.px(_NAME_X), mid + self.px(1))
        c.itemconfigure(it["name"], text=shown)
        c.coords(it["publisher"], self.px(_NAME_X), mid + self.px(3))
        c.itemconfigure(it["publisher"], text=self.truncate(program.get("publisher") or "—", name_px, self.font(11)))

        store = program.get("store")
        if store:
            bx = w - self.px(COL_RIGHT + COL_SIZE + COL_BADGE / 2)
            c.coords(it["badge_bg"], bx, mid)
            c.itemconfigure(it["badge_bg"], image=pill_image(self.px(50), self.px(20), theme.BORDER, self.px(6)),
                            state="normal")
            c.coords(it["badge"], bx, mid)
            c.itemconfigure(it["badge"], text=store, fill=STORE_COLORS.get(store, theme.TEXT_DIM), state="normal")

        self._bind_size(slot, program)
        c.coords(it["date"], w - self.px(16), mid)
        c.itemconfigure(it["date"], text=relative_date(program.get("install_date")))

        for region, x0, y0, x1, y1 in self._button_boxes(program):
            c.coords(it[f"{region}_bg"], x0, y0)
            c.coords(it[f"{region}_text"], (x0 + x1) / 2, (y0 + y1) / 2)
        slot.data["hovered"] = None
        self._apply_state(slot, index)

    def _bind_size(self, slot: Slot, program: dict) -> None:
        c = self.canvas
        it = slot.items
        mid = self.px(CARD_H) // 2
        right = self.width - self.px(COL_RIGHT + 14)
        c.coords(it["size"], right, mid + self.px(1))
        c.itemconfigure(it["size"], text=size_text(program))
        bar_w = self.px(COL_SIZE - 14)
        y = mid + self.px(10)
        half = self.px(2)
        x0, x1 = right - bar_w + half, right - half
        c.coords(it["bar_track"], x0, y, x1, y)
        size = program["size_bytes"]
        frac = max(size / self.max_size, 0.02) if size and self.max_size else 0
        if frac:
            c.coords(it["bar"], x0, y, x0 + (x1 - x0) * min(frac, 1.0), y)
            c.itemconfigure(it["bar"], fill=CATEGORY_COLORS.get(program.get("category"), theme.ACCENT_GREEN),
                            state="normal")
        else:
            c.itemconfigure(it["bar"], state="hidden")

    def _apply_state(self, slot: Slot, index: int) -> None:
        """Фон картки й чекбокс за станом (наведення / вибір)."""
        program = self._items[index]
        hovered = self._hover[0] == index
        selected = program["key"] in self.selected
        region = self._hover[1] if hovered else None
        if hovered:
            fill, border = theme.BG_PANEL_LIGHT, theme.ACCENT_BLUE
        elif selected:
            fill, border = theme.BG_PANEL, theme.ACCENT_GREEN_DIM
        else:
            fill, border = theme.BG_PANEL, theme.BORDER
        c = self.canvas
        it = slot.items
        c.itemconfigure(it["bg"], image=card_image(
            max(self.width, 40), self.px(CARD_H), self.px(CARD_RADIUS), fill, border, self.bg, max(1, self.px(1)),
        ))
        c.itemconfigure(it["check"], image=checkbox_image(
            self.px(_CHECK_DP), selected, region == "check", False, self.S,
        ))

        if hovered != slot.data.get("hovered"):
            slot.data["hovered"] = hovered
            c.itemconfigure(it["date"], state="hidden" if hovered else "normal")
            boxes = {b[0] for b in self._button_boxes(program)} if hovered else set()
            for name, _text, _w, _r in _BUTTONS:
                state = "normal" if name in boxes else "hidden"
                c.itemconfigure(it[f"{name}_bg"], state=state)
                c.itemconfigure(it[f"{name}_text"], state=state)
        if hovered:
            for name, _text, w_dp, _r in _BUTTONS:
                normal, hover = (theme.ERROR, _DELETE_HOVER) if name == "delete" else (theme.BORDER, _OPEN_HOVER)
                self.iset(slot, f"{name}_bg", image=pill_image(
                    self.px(w_dp), self.px(_BUTTON_H), hover if region == name else normal, self.px(8),
                ))

    # ------------------------------------------------------------- події

    def hover_slot(self, slot: Slot, index: int, region: str | None) -> None:
        self._apply_state(slot, index)

    def hit_test(self, index: int, x: int, y: int) -> str | None:
        if y >= self.px(CARD_H):
            return None  # проміжок між картками
        if x < self.px(_ICON_X - 6):
            return "check"
        program = self._items[index]
        for region, x0, y0, x1, y1 in self._button_boxes(program):
            if x0 <= x < x1 and y0 <= y < y1:
                return region
        if self.px(_NAME_X) <= x < self.px(_NAME_X) + self._name_px():
            return "name"
        if x >= self.width - self.px(COL_RIGHT):
            return "date"
        return "row"

    def click(self, index: int, region: str) -> None:
        program = self._items[index]
        if region == "check":
            checked = program["key"] not in self.selected
            if checked:
                self.selected.add(program["key"])
            else:
                self.selected.discard(program["key"])
            slot = self._slots.get(index)
            if slot is not None:
                self._apply_state(slot, index)
            self.on_toggle(program, checked)
        elif region == "open":
            self.on_open(program)
        elif region == "delete":
            self.on_uninstall(program)

    def double_click(self, index: int, region: str) -> None:
        if region not in ("check", "open", "delete"):
            self.on_open(self._items[index])

    def tooltip_for(self, index: int, region: str) -> str | None:
        program = self._items[index]
        slot = self._slots.get(index)
        if region == "name" and slot is not None and slot.data.get("name_truncated"):
            version = program.get("version")
            return f"{program['name']}\nВерсія: {version}" if version else program["name"]
        if region == "date":
            d = program.get("install_date")
            if d is not None:
                return f"Встановлено: {d.strftime('%d.%m.%Y')}"
        return None

    def on_scale_changed(self) -> None:
        self._photo_cache.clear()

    # -------------------------------------------------------------- іконки

    def icon_photo(self, program: dict):
        """PhotoImage іконки програми (реальна, якщо вже витягнута, інакше заглушка)."""
        S = self.S
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
        except (RuntimeError, Exception):
            pass

    def _on_icon_ready(self, key: str) -> None:
        if not self.winfo_exists():
            return
        for idx, slot in self._slots.items():
            program = self._items[idx]
            if program["key"] == key:
                self.canvas.itemconfigure(slot.items["icon"], image=self.icon_photo(program))
