"""Робот PulseFPS — один спільний малюнок для всієї програми (Монітор, Ігровий
режим, лого в меню, робот-прибиральник на Очищенні / Мережі / Системі та у
вікнах анімації).

Вигляд — як на логотипі (tools/gen_assets.py): кругле тіло з блакитним
вертикальним градієнтом #4fc3ff -> #1f7ae0, темний екран-обличчя із
заокругленими кутами, зелені очі-дужки #2ee59d, антена із зеленим вогником,
рожевий рум'янець.

Пропорції: робот малюється в КВАДРАТІ 100x100 умовних одиниць, тіло — справжнє
коло (центр 50,58, радіус 35), тож на будь-якому масштабі екрана й розмірі
полотна голова не сплющується. RobotView завжди бере квадрат
min(ширина, висота) і ставить його по центру полотна.

Настрої: calm (спокійний), happy (радий), sad (сумний), worried (стурбований),
gaming (ігровий, у навушниках, яскраві очі зі свіченням), sleepy (сонний, очі-
риски — вимкнений ігровий режим). Анімація: кліпання, «дихання» (легке
погойдування) і пульс вогника антени / свічення очей — 4 фази-спрайти.
"""

from __future__ import annotations

import math
import random
import time
import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFilter, ImageTk

from ui import theme
from ui.widgets import aa

CALM, HAPPY, SAD, WORRIED, GAMING, SLEEPY = "calm", "happy", "sad", "worried", "gaming", "sleepy"
MOODS = (CALM, HAPPY, SAD, WORRIED, GAMING, SLEEPY)

UNIT = 100  # сторона квадрата робота в умовних одиницях
BODY_CX, BODY_CY, BODY_R = 50, 58, 35
ANTENNA_TIP = (50, 9)  # центр вогника
GLOW_PHASES = 4

_BODY_TOP = aa.rgb("#4fc3ff")
_BODY_BOTTOM = aa.rgb("#1f7ae0")
_SCREEN = aa.rgb("#0d1321")
_GREEN = aa.rgb("#2ee59d")
_GREEN_SLEEPY = aa.rgb("#5c9f86")
_ORANGE = aa.rgb("#e0a52f")
_STICK = aa.rgb("#8a94a6")
_BLUSH = aa.rgb("#ff6fa3")
_BAND = aa.rgb("#3b4b70")
_HIGHLIGHT = aa.rgb("#d8fff0")
_GLOW_STRENGTH = (0.55, 0.75, 0.95, 0.75)  # «дихання» свічення за фазами


def light_color(mood: str) -> tuple:
    """Колір вогника антени: стурбований — помаранчевий, сонний — приглушений."""
    if mood == WORRIED:
        return _ORANGE
    return _GREEN_SLEEPY if mood == SLEEPY else _GREEN


# ================================================================ малювання

class _Canvas:
    """Малювання в одиницях квадрата робота на суперсемпльованому шарі (aa.SS).
    (x0, y0) — лівий верхній кут квадрата у фінальних пікселях шару, side — його
    сторона у фінальних пікселях."""

    def __init__(self, layer: Image.Image, x0: float, y0: float, side: float):
        self.layer = layer
        self.p = aa.Painter(layer, side / UNIT, ox=-x0, oy=-y0)
        self.k = self.p.k

    def xy(self, x: float, y: float) -> tuple[float, float]:
        return self.p._p(x, y)

    def blend(self, color: tuple, mask: Image.Image) -> None:
        """Накласти колір через маску (L, розміру шару) — правильно і для RGB, і для RGBA."""
        if self.layer.mode == "RGBA":
            overlay = Image.new("RGBA", self.layer.size, color + (0,))
            overlay.putalpha(mask)
            self.layer.alpha_composite(overlay)
        else:
            self.layer.paste(Image.new("RGB", self.layer.size, color), (0, 0), mask)

    def soft_ellipses(self, centers, rx: float, ry: float, color: tuple, alpha: int, blur: float = 0) -> None:
        """Напівпрозорі (і за потреби розмиті) еліпси: свічення, рум'янець."""
        mask = Image.new("L", self.layer.size, 0)
        d = ImageDraw.Draw(mask)
        for cx, cy in centers:
            ax, ay = self.xy(cx - rx, cy - ry)
            bx, by = self.xy(cx + rx, cy + ry)
            d.ellipse((ax, ay, bx, by), fill=alpha)
        if blur:
            mask = mask.filter(ImageFilter.GaussianBlur(radius=blur * self.k))
        self.blend(color, mask)

    def gradient_circle(self, cx: float, cy: float, r: float, top: tuple, bottom: tuple) -> None:
        ax, ay = self.xy(cx - r, cy - r)
        bx, by = self.xy(cx + r, cy + r)
        box = (math.floor(ax), math.floor(ay), math.ceil(bx), math.ceil(by))
        w, h = box[2] - box[0], box[3] - box[1]
        if w <= 0 or h <= 0:
            return
        column = Image.new("RGB", (1, h))
        for y in range(h):
            t = y / max(h - 1, 1)
            column.putpixel((0, y), tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
        grad = column.resize((w, h))
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).ellipse((ax - box[0], ay - box[1], bx - box[0], by - box[1]), fill=255)
        if self.layer.mode == "RGBA":
            grad = grad.convert("RGBA")
        self.layer.paste(grad, box[:2], mask)


def paint_robot(layer: Image.Image, x0: float, y0: float, side: float, mood: str = CALM, *,
                blink: bool = False, glow_phase: int = 1, light: bool = True) -> None:
    """Намалювати робота в квадраті (x0, y0, side — фінальні пікселі) на
    суперсемпльованому шарі `layer` (aa.new_layer). light=False — без вогника
    антени (сцена, що анімує його окремим спрайтом, як робот-прибиральник)."""
    c = _Canvas(layer, x0, y0, side)
    p = c.p
    strength = _GLOW_STRENGTH[glow_phase % GLOW_PHASES]
    gaming = mood == GAMING
    eye = _GREEN_SLEEPY if mood == SLEEPY else _GREEN
    lx, ly = ANTENNA_TIP

    # --- антена (за тілом) і свічення вогника
    if light:
        c.soft_ellipses([(lx, ly)], 8, 8, light_color(mood), round(120 * strength), blur=3)
    p.line([(50, BODY_CY - BODY_R + 2), (50, ly + 3)], fill=_STICK, width=2.4)

    # --- дуга навушників над головою
    if gaming:
        r = BODY_R + 6
        p.arc(BODY_CX - r, BODY_CY - r, BODY_CX + r, BODY_CY + r, start=18, extent=144,
              fill=_BAND, width=5.5)
        p.arc(BODY_CX - r + 1, BODY_CY - r + 1, BODY_CX + r - 1, BODY_CY + r - 1, start=24, extent=132,
              fill=_GREEN, width=1.4)

    # --- тіло: коло з градієнтом і тонким обідком
    c.gradient_circle(BODY_CX, BODY_CY, BODY_R, _BODY_TOP, _BODY_BOTTOM)
    p.ellipse(BODY_CX - BODY_R, BODY_CY - BODY_R, BODY_CX + BODY_R, BODY_CY + BODY_R,
              outline=_GREEN if gaming else _BODY_BOTTOM, width=1.6)

    # --- рум'янець
    c.soft_ellipses([(30, 78), (70, 78)], 5.5, 3, _BLUSH, 150 if mood != SLEEPY else 90, blur=0.8)

    # --- чашки навушників і мікрофон
    if gaming:
        p.line([(14, 70), (17, 81), (30, 86)], fill=_BAND, width=2.4)
        p.ellipse(28, 83, 34, 89, fill=_GREEN)
        for cx in (13, 87):
            p.ellipse(cx - 7, 44, cx + 7, 72, fill=_BAND, outline=_GREEN, width=1.5)

    # --- екран-обличчя: заокруглений прямокутник
    sx0, sy0 = c.xy(27, 41)
    sx1, sy1 = c.xy(73, 71)
    p.d.rounded_rectangle((sx0, sy0, sx1, sy1), radius=8.5 * c.k, fill=_SCREEN)

    # --- очі
    if gaming and not blink:
        c.soft_ellipses([(40, 54), (60, 54)], 8, 8.5, _GREEN, round(235 * strength), blur=3.2)
    _paint_eyes(p, mood, blink, eye)
    _paint_mouth(p, mood, eye)

    # --- вогник антени (поверх усього)
    if light:
        r = 4.2 if mood != SLEEPY else 3.6
        p.ellipse(lx - r, ly - r, lx + r, ly + r, fill=light_color(mood))
        p.ellipse(lx - r * 0.55, ly - r * 0.6, lx - r * 0.05, ly - r * 0.1, fill=_HIGHLIGHT)


def _paint_eyes(p: aa.Painter, mood: str, blink: bool, eye: tuple) -> None:
    lw = 2.6
    if blink or mood == SLEEPY:
        y = 56 if mood == SLEEPY else 54
        for cx in (40, 60):
            p.line([(cx - 4.5, y), (cx + 4.5, y)], fill=eye, width=lw)
        return
    if mood == HAPPY:  # «^ ^»
        for cx in (40, 60):
            p.arc(cx - 5, 51, cx + 5, 61, start=20, extent=140, fill=eye, width=lw)
        return
    if mood == GAMING:  # яскраві овали з відблиском
        for cx in (40, 60):
            p.ellipse(cx - 5, 47, cx + 5, 61, fill=eye)
            p.ellipse(cx - 3, 49, cx + 0.2, 53, fill=_HIGHLIGHT)
        return
    if mood == SAD:  # опущені брови (зовнішній край нижче) і менші очі
        for cx, side in ((40, -1), (60, 1)):
            p.line([(cx - side * 4.5, 46.5), (cx + side * 4.5, 49.5)], fill=eye, width=2)
            p.ellipse(cx - 2.6, 52.5, cx + 2.6, 59, fill=eye)
        return
    # спокійний / стурбований — дужки «( )», як на логотипі
    for cx, start in ((40, 110), (60, -70)):
        p.arc(cx - 3.6, 47, cx + 3.6, 61, start=start, extent=140, fill=eye, width=lw)
    if mood == WORRIED:  # підняті до середини брови
        for cx, side in ((40, -1), (60, 1)):
            p.line([(cx + side * 4.5, 45), (cx - side * 3, 42.5)], fill=eye, width=2)


def _paint_mouth(p: aa.Painter, mood: str, eye: tuple) -> None:
    if mood == HAPPY:
        p.arc(42, 57, 58, 68, start=200, extent=140, fill=eye, width=2.4)
    elif mood == GAMING:
        p.arc(43, 58, 57, 67, start=200, extent=140, fill=eye, width=2.2)
    elif mood == SAD:
        p.arc(44, 63, 56, 70, start=25, extent=130, fill=eye, width=2.2)
    elif mood == WORRIED:
        pts = [(43 + i * 2.8, 66 + (1.1 if i % 2 else -1.1)) for i in range(6)]
        p.line(pts, fill=eye, width=1.8)
    elif mood == SLEEPY:
        p.line([(47, 64.5), (53, 64.5)], fill=eye, width=2)
    else:
        p.arc(44.5, 59, 55.5, 66, start=200, extent=140, fill=eye, width=2.2)


def render_robot(side_px: int, mood: str = CALM, *, blink: bool = False, glow_phase: int = 1,
                 bg: str | None = None) -> Image.Image:
    """Готове квадратне зображення робота side_px x side_px (RGB на тлі bg або RGBA)."""
    side_px = max(8, int(side_px))
    layer = aa.new_layer(side_px, side_px, "RGB", aa.rgb(bg)) if bg else aa.new_layer(side_px, side_px)
    paint_robot(layer, 0, 0, side_px, mood, blink=blink, glow_phase=glow_phase)
    return aa.downscale(layer, (side_px, side_px))


_photo_cache: dict = {}
_PHOTO_CACHE_MAX = 96


def robot_photo(side_px: int, mood: str, blink: bool, glow_phase: int, bg: str) -> ImageTk.PhotoImage:
    """PhotoImage робота зі спільного кешу (однакові роботи на різних вкладках не малюються двічі)."""
    key = (side_px, mood, blink, glow_phase, bg)
    photo = _photo_cache.get(key)
    if photo is None:
        if len(_photo_cache) >= _PHOTO_CACHE_MAX:
            _photo_cache.pop(next(iter(_photo_cache)))
        photo = _photo_cache[key] = ImageTk.PhotoImage(
            render_robot(side_px, mood, blink=blink, glow_phase=glow_phase, bg=bg))
    return photo


# =================================================================== віджет

_TICK_MS = 50
_BREATH_PERIOD_S = 3.2
_BREATH_UNITS = 1.2  # амплітуда погойдування, одиниць квадрата
_GLOW_STEP_S = 0.45


class RobotView(ctk.CTkFrame):
    """Робот на власному tk.Canvas. size — бажана сторона в dp (100%-й масштаб);
    якщо полотно розтягнуте менеджером геометрії, робот бере квадрат
    min(ширина, висота) і стоїть по центру — пропорції завжди 1:1.

    set_mood() — настрій, set_running() — анімація (лише поки робота видно),
    animate=False — статичний робот."""

    def __init__(self, master, size: float = 88, mood: str = CALM, bg: str = theme.BG_PANEL,
                 animate: bool = True):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.S = self._get_widget_scaling()
        self._size_dp = size
        self._bg = bg
        self._mood = mood if mood in MOODS else CALM
        self._animate = animate
        self._blink = False
        self._blink_timer = random.uniform(1.6, 3.0)
        self._glow_phase = 1
        self._t0 = time.perf_counter()
        self._last_tick = None
        self._after_id = None
        self._area = (0, 0)
        self._side = 0

        px = round(size * self.S)
        self.canvas = tk.Canvas(self, width=px, height=px, bg=bg, highlightthickness=0, bd=0, takefocus=0)
        self.canvas.pack(fill="both", expand=True)
        self._image = self.canvas.create_image(0, 0, anchor="center")
        self.canvas.bind("<Configure>", self._on_configure)
        self.bind("<Destroy>", self._on_destroy)
        self._area = (px, px)
        self._redraw()

    # ------------------------------------------------------------ публічне

    @property
    def mood(self) -> str:
        return self._mood

    def set_mood(self, mood: str) -> None:
        mood = mood if mood in MOODS else CALM
        if mood != self._mood:
            self._mood = mood
            self._redraw()

    def set_running(self, running: bool) -> None:
        if running and self._animate and self._after_id is None:
            self._last_tick = None
            self._tick()
        elif not running and self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None

    # ----------------------------------------------------------- розміри

    def _set_scaling(self, *args, **kwargs):
        super()._set_scaling(*args, **kwargs)
        if not hasattr(self, "canvas"):
            return
        self.S = args[0]
        px = round(self._size_dp * self.S)
        self.canvas.configure(width=px, height=px)

    def _on_configure(self, event) -> None:
        if (event.width, event.height) != self._area:
            self._area = (event.width, event.height)
            self._redraw()

    # ----------------------------------------------------------- малювання

    def _breath_offset(self) -> int:
        if self._after_id is None or not theme.robot_animation_enabled():
            return 0
        t = time.perf_counter() - self._t0
        return round(math.sin(t * 2 * math.pi / _BREATH_PERIOD_S) * _BREATH_UNITS * self._side / UNIT)

    def _redraw(self) -> None:
        w, h = self._area
        side = min(w, h)
        if side < 8:
            return
        self._side = side
        photo = robot_photo(side, self._mood, self._blink, self._glow_phase, self._bg)
        self.canvas.itemconfigure(self._image, image=photo)
        self.canvas.coords(self._image, w // 2, h // 2 + self._breath_offset())

    def _tick(self) -> None:
        if not self.winfo_exists():
            self._after_id = None
            return
        if theme.robot_animation_enabled():
            now = time.perf_counter()
            dt = 0.0 if self._last_tick is None else min(now - self._last_tick, 0.5)
            self._last_tick = now
            changed = False
            self._blink_timer -= dt
            if self._blink_timer <= 0:
                self._blink = not self._blink
                self._blink_timer = random.uniform(0.12, 0.2) if self._blink else random.uniform(2.0, 4.0)
                changed = True
            phase = int((now - self._t0) / _GLOW_STEP_S) % GLOW_PHASES
            if phase != self._glow_phase:
                self._glow_phase = phase
                changed = True
            if changed:
                self._redraw()
            else:
                w, h = self._area
                self.canvas.coords(self._image, w // 2, h // 2 + self._breath_offset())
            delay = _TICK_MS
        else:
            self._last_tick = None
            delay = 200
        self._after_id = self.after(delay, self._tick)

    def _on_destroy(self, event) -> None:
        if event.widget is self and self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
