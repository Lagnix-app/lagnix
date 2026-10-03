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

Згладжування: Pillow із суперсемплінгом 4x (ui/widgets/aa.py) і зменшенням
LANCZOS — як кільця на «Моніторі».

Анімація — готові кадри, а не малювання на льоту: для кожного (розмір, настрій,
тло) один раз генерується набір — FRAMES_BREATH кадрів «дихання» (зсув на частки
пікселя зашитий у сам рендер, тож рух плавний, без стрибків по цілому пікселю;
разом із ним пульсує свічення) і FRAMES_BLINK кадрів кліпання (повіки плавно
закриваються й відкриваються). Кадри рендеряться у фоновому потоці й лежать у
спільному кеші як PhotoImage; таймер (~60 к/с, від реального часу) лише
перемикає їх. Перегенерація — тільки при зміні розміру чи масштабу екрана.

Настрої: calm (спокійний), happy (радий), sad (сумний), worried (стурбований),
gaming (ігровий, у навушниках, яскраві очі зі свіченням), sleepy (сонний, очі-
риски — вимкнений ігровий режим).
"""

from __future__ import annotations

import math
import random
import threading
import time
import tkinter as tk
from collections import OrderedDict

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFilter, ImageTk

from core.logging_setup import get_logger
from ui import theme
from ui.widgets import aa

_logger = get_logger(__name__)

CALM, HAPPY, SAD, WORRIED, GAMING, SLEEPY = "calm", "happy", "sad", "worried", "gaming", "sleepy"
MOODS = (CALM, HAPPY, SAD, WORRIED, GAMING, SLEEPY)

UNIT = 100  # сторона квадрата робота в умовних одиницях
BODY_CX, BODY_CY, BODY_R = 50, 58, 35
ANTENNA_TIP = (50, 9)  # центр вогника

FRAMES_BREATH = 30
FRAMES_BLINK = 6
BREATH_PERIOD_S = 3.2
BREATH_UNITS = 1.3  # амплітуда погойдування, одиниць квадрата
BLINK_S = 0.18  # тривалість кліпання
# закритість повік по кадрах кліпання (0 — відкриті, 1 — закриті)
_BLINK_CURVE = (0.35, 0.75, 1.0, 1.0, 0.7, 0.3)

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

    def _region(self, x0: float, y0: float, x1: float, y1: float) -> tuple[int, int, int, int] | None:
        """Прямокутник (пікселі шару) в межах шару або None."""
        ax, ay = self.xy(x0, y0)
        bx, by = self.xy(x1, y1)
        W, H = self.layer.size
        box = (max(0, math.floor(ax)), max(0, math.floor(ay)), min(W, math.ceil(bx)), min(H, math.ceil(by)))
        return box if box[2] > box[0] and box[3] > box[1] else None

    def soft_ellipses(self, centers, rx: float, ry: float, color: tuple, alpha: int, blur: float = 0) -> None:
        """Напівпрозорі (і за потреби розмиті) еліпси: свічення, рум'янець. Маска
        й розмиття — лише в межах еліпсів із запасом на розмиття, а не на весь шар."""
        margin = blur * 3
        box = self._region(min(c[0] for c in centers) - rx - margin, min(c[1] for c in centers) - ry - margin,
                           max(c[0] for c in centers) + rx + margin, max(c[1] for c in centers) + ry + margin)
        if box is None or alpha <= 0:
            return
        mask = Image.new("L", (box[2] - box[0], box[3] - box[1]), 0)
        d = ImageDraw.Draw(mask)
        for cx, cy in centers:
            ax, ay = self.xy(cx - rx, cy - ry)
            bx, by = self.xy(cx + rx, cy + ry)
            d.ellipse((ax - box[0], ay - box[1], bx - box[0], by - box[1]), fill=alpha)
        if blur:
            mask = mask.filter(ImageFilter.GaussianBlur(radius=blur * self.k))
        if self.layer.mode == "RGBA":
            overlay = Image.new("RGBA", mask.size, color + (0,))
            overlay.putalpha(mask)
            self.layer.alpha_composite(overlay, dest=box[:2])
        else:
            self.layer.paste(Image.new("RGB", mask.size, color), box[:2], mask)

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
                blink: float = 0.0, breath: float = 0.0, light: bool = True) -> None:
    """Намалювати робота в квадраті (x0, y0, side — фінальні пікселі) на
    суперсемпльованому шарі `layer` (aa.new_layer).

    blink — закритість повік 0..1 (True/False теж можна); breath — фаза дихання
    -1..1: зсув униз-угору на частки пікселя і сила свічення; light=False — без
    вогника антени (сцена, що анімує його окремим спрайтом, як прибиральник)."""
    blink = float(blink)
    c = _Canvas(layer, x0, y0 + breath * BREATH_UNITS * side / UNIT, side)
    p = c.p
    strength = 0.75 + 0.2 * breath  # свічення «дихає» разом із тілом
    gaming = mood == GAMING
    eye = _GREEN_SLEEPY if mood == SLEEPY else _GREEN
    lx, ly = ANTENNA_TIP

    # --- антена (за тілом) і свічення вогника
    if light:
        c.soft_ellipses([(lx, ly)], 7, 7, light_color(mood), round(120 * strength), blur=2.5)
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
    openness = 1.0 - max(0.0, min(blink, 1.0))
    if gaming and openness > 0.2:
        c.soft_ellipses([(40, 54), (60, 54)], 8, 8.5 * openness, _GREEN, round(235 * strength * openness),
                        blur=3.2)
    _paint_eyes(p, mood, openness, eye)
    _paint_mouth(p, mood, eye)

    # --- вогник антени (поверх усього)
    if light:
        r = 4.2 if mood != SLEEPY else 3.6
        p.ellipse(lx - r, ly - r, lx + r, ly + r, fill=light_color(mood))
        p.ellipse(lx - r * 0.55, ly - r * 0.6, lx - r * 0.05, ly - r * 0.1, fill=_HIGHLIGHT)


def _squash(cy: float, y: float, openness: float) -> float:
    """Стиснути координату y ока до його центру cy (повіка закривається)."""
    return cy + (y - cy) * openness


def _paint_eyes(p: aa.Painter, mood: str, openness: float, eye: tuple) -> None:
    lw = 2.6
    if openness <= 0.22 or mood == SLEEPY:
        y = 56 if mood == SLEEPY else 54
        for cx in (40, 60):
            p.line([(cx - 4.5, y), (cx + 4.5, y)], fill=eye, width=lw)
        return
    o = openness
    if mood == HAPPY:  # «^ ^»
        for cx in (40, 60):
            p.arc(cx - 5, _squash(56, 51, o), cx + 5, _squash(56, 61, o), start=20, extent=140, fill=eye, width=lw)
        return
    if mood == GAMING:  # яскраві овали з відблиском
        for cx in (40, 60):
            p.ellipse(cx - 5, _squash(54, 47, o), cx + 5, _squash(54, 61, o), fill=eye)
            if o > 0.6:
                p.ellipse(cx - 3, _squash(54, 49, o), cx + 0.2, _squash(54, 53, o), fill=_HIGHLIGHT)
        return
    if mood == SAD:  # опущені брови (внутрішній край вище) і менші очі
        for cx, side in ((40, -1), (60, 1)):
            p.line([(cx - side * 4.5, 46.5), (cx + side * 4.5, 49.5)], fill=eye, width=2)
            p.ellipse(cx - 2.6, _squash(55.7, 52.5, o), cx + 2.6, _squash(55.7, 59, o), fill=eye)
        return
    # спокійний / стурбований — дужки «( )», як на логотипі
    for cx, start in ((40, 110), (60, -70)):
        p.arc(cx - 3.6, _squash(54, 47, o), cx + 3.6, _squash(54, 61, o), start=start, extent=140,
              fill=eye, width=lw)
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


def render_robot(side_px: int, mood: str = CALM, *, blink: float = 0.0, breath: float = 0.0,
                 bg: str | None = None) -> Image.Image:
    """Готове квадратне зображення робота side_px x side_px (RGB на тлі bg або RGBA):
    4x суперсемплінг -> LANCZOS."""
    side_px = max(8, int(side_px))
    layer = aa.new_layer(side_px, side_px, "RGB", aa.rgb(bg)) if bg else aa.new_layer(side_px, side_px)
    paint_robot(layer, 0, 0, side_px, mood, blink=blink, breath=breath)
    return aa.downscale(layer, (side_px, side_px))


# ============================================================ набори кадрів

def _breath_value(i: int) -> float:
    return math.sin(2 * math.pi * i / FRAMES_BREATH)


class FrameSet:
    """Кадри одного (розмір, настрій, тло): static — одразу (кадр спокою), решта —
    у фоновому потоці. PhotoImage створюються в потоці UI при першому показі."""

    def __init__(self, side: int, mood: str, bg: str):
        self.side, self.mood, self.bg = side, mood, bg
        self.static = ImageTk.PhotoImage(render_robot(side, mood, bg=bg))
        self._images: tuple[list, list] | None = None  # (дихання, кліпання) — PIL
        self.breath: list | None = None  # PhotoImage
        self.blink: list | None = None
        threading.Thread(target=self._render_all, daemon=True, name="robot-frames").start()

    def _render_all(self) -> None:
        try:
            breath = [render_robot(self.side, self.mood, breath=_breath_value(i), bg=self.bg)
                      for i in range(FRAMES_BREATH)]
            blink = [render_robot(self.side, self.mood, blink=b, bg=self.bg) for b in _BLINK_CURVE]
            self._images = (breath, blink)
        except Exception:
            _logger.exception("Failed to generate robot frames")

    def ready(self) -> bool:
        """Кадри готові (PhotoImage створюються тут — викликати з потоку UI)."""
        if self.breath is not None:
            return True
        if self._images is None:
            return False
        breath, blink = self._images
        self.breath = [ImageTk.PhotoImage(img) for img in breath]
        self.blink = [ImageTk.PhotoImage(img) for img in blink]
        self._images = None
        return True


_frame_sets: OrderedDict = OrderedDict()
_FRAME_SETS_MAX = 16  # ~36 кадрів на набір; стара DPI/розміри витісняються


def frame_set(side: int, mood: str, bg: str) -> FrameSet:
    """Спільний набір кадрів: однакові роботи на різних вкладках не генеруються двічі."""
    key = (side, mood, bg)
    fs = _frame_sets.get(key)
    if fs is None:
        fs = _frame_sets[key] = FrameSet(side, mood, bg)
        while len(_frame_sets) > _FRAME_SETS_MAX:
            _frame_sets.popitem(last=False)
    else:
        _frame_sets.move_to_end(key)
    return fs


# =================================================================== віджет

_TICK_MS = 16  # ~60 к/с; рух рахується від реального часу, а не від номера тіку


class RobotView(ctk.CTkFrame):
    """Робот на власному tk.Canvas. size — бажана сторона в dp (100%-й масштаб);
    якщо полотно розтягнуте менеджером геометрії, робот бере квадрат
    min(ширина, висота) і стоїть по центру — пропорції завжди 1:1.

    set_mood() — настрій, set_running() — анімація (лише поки робота видно:
    прихована вкладка / згорнуте вікно — пауза), animate=False — статичний робот."""

    def __init__(self, master, size: float = 88, mood: str = CALM, bg: str = theme.BG_PANEL,
                 animate: bool = True):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.S = self._get_widget_scaling()
        self._size_dp = size
        self._bg = bg
        self._mood = mood if mood in MOODS else CALM
        self._animate = animate
        self._frames: FrameSet | None = None
        self._shown = None
        self._after_id = None
        self._last_tick = None
        self._breath_t = random.uniform(0, BREATH_PERIOD_S)  # час дихання (стоїть під час кліпання)
        self._blink_left = random.uniform(1.6, 3.0)  # до наступного кліпання
        self._blink_t: float | None = None  # скільки триває поточне кліпання
        self._area = (0, 0)

        px = round(size * self.S)
        self.canvas = tk.Canvas(self, width=px, height=px, bg=bg, highlightthickness=0, bd=0, takefocus=0)
        self.canvas.pack(fill="both", expand=True)
        self._image = self.canvas.create_image(px // 2, px // 2, anchor="center")
        self.canvas.bind("<Configure>", self._on_configure)
        self.bind("<Destroy>", self._on_destroy)
        self._area = (px, px)
        self._load_frames()

    # ------------------------------------------------------------ публічне

    @property
    def mood(self) -> str:
        return self._mood

    def set_mood(self, mood: str) -> None:
        mood = mood if mood in MOODS else CALM
        if mood != self._mood:
            self._mood = mood
            self._load_frames()

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
        self.canvas.configure(width=px, height=px)  # новий розмір -> <Configure> -> нові кадри

    def _on_configure(self, event) -> None:
        if (event.width, event.height) != self._area:
            self._area = (event.width, event.height)
            self.canvas.coords(self._image, event.width // 2, event.height // 2)
            self._load_frames()

    # ----------------------------------------------------------- кадри

    def _load_frames(self) -> None:
        side = min(self._area)
        if side < 8:
            return
        self._frames = frame_set(side, self._mood, self._bg)
        self._show_current()

    def _current_photo(self):
        fs = self._frames
        if fs is None:
            return None
        if self._after_id is None or not theme.robot_animation_enabled() or not fs.ready():
            return fs.static
        if self._blink_t is not None:
            i = min(int(self._blink_t / BLINK_S * FRAMES_BLINK), FRAMES_BLINK - 1)
            return fs.blink[i]
        i = int(self._breath_t / BREATH_PERIOD_S * FRAMES_BREATH) % FRAMES_BREATH
        return fs.breath[i]

    def _show_current(self) -> None:
        photo = self._current_photo()
        if photo is not None and photo is not self._shown:
            self._shown = photo
            self.canvas.itemconfigure(self._image, image=photo)

    def _tick(self) -> None:
        if not self.winfo_exists():
            self._after_id = None
            return
        if theme.is_scrolling():  # під час прокрутки кадри не малюємо
            self._last_tick = None
            self._after_id = self.after(50, self._tick)
            return
        if theme.robot_animation_enabled():
            now = time.perf_counter()
            dt = 0.0 if self._last_tick is None else min(now - self._last_tick, 0.1)
            self._last_tick = now
            if self._blink_t is not None:
                self._blink_t += dt
                if self._blink_t >= BLINK_S:
                    self._blink_t = None
                    self._blink_left = random.uniform(2.2, 4.5)
            else:
                self._breath_t = (self._breath_t + dt) % BREATH_PERIOD_S
                self._blink_left -= dt
                # кліпаємо, коли дихання проходить через середнє положення: кадри
                # кліпання намальовані саме в ньому, тож ривка немає
                phase = self._breath_t / BREATH_PERIOD_S * FRAMES_BREATH
                if self._blink_left <= 0 and (round(phase) % (FRAMES_BREATH // 2) == 0):
                    self._blink_t = 0.0
            delay = _TICK_MS
        else:
            self._last_tick = None
            delay = 200
        # поки кадри генеруються, другий тік із _after_id=None показав би static
        self._after_id = self.after(delay, self._tick)
        self._show_current()

    def _on_destroy(self, event) -> None:
        if event.widget is self and self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
