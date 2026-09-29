"""Згладжене (anti-aliased) малювання через Pillow для віджетів на tk.Canvas.

tkinter Canvas не має антиаліасингу, тому фігури, які мають виглядати гладко
(кільця, графік, роботи), малюються в Pillow у SS-кратному розмірі й
зменшуються через Image.resize(..., LANCZOS) (суперсемплінг). Painter дозволяє
малювати в «логічних» одиницях (dp, як у 100%-му масштабі), а множник
масштабу екрана (DPI) й суперсемплінг застосовує сам — тому на 125%/150%
картинка чітка, а не розмита розтягуванням.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

SS = 4  # коефіцієнт суперсемплінгу

_LANCZOS = Image.Resampling.LANCZOS


def rgb(color: str, alpha: int | None = None) -> tuple:
    color = color.lstrip("#")
    value = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    return value if alpha is None else value + (alpha,)


def downscale(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Зменшує суперсемпльоване зображення до фінального розміру: LANCZOS,
    але з reducing_gap — Pillow спершу вдвічі зменшує box-фільтром (той самий
    антиаліасинг), а LANCZOS застосовує до вдвічі меншої картинки: ~1.5-2x
    швидше за чистий LANCZOS з практично тим самим результатом."""
    return img.resize(size, _LANCZOS, reducing_gap=2.0)


def catmull_rom(points: list[tuple[float, float]], samples: int = 8) -> list[tuple[float, float]]:
    """Гладка крива Catmull-Rom через задані точки (по `samples` відрізків на сегмент)."""
    n = len(points)
    if n < 3:
        return list(points)
    out: list[tuple[float, float]] = []
    for i in range(n - 1):
        p0 = points[max(i - 1, 0)]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[min(i + 2, n - 1)]
        for j in range(samples):
            t = j / samples
            t2 = t * t
            t3 = t2 * t
            out.append((
                0.5 * (2 * p1[0] + (p2[0] - p0[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (3 * p1[0] - p0[0] - 3 * p2[0] + p3[0]) * t3),
                0.5 * (2 * p1[1] + (p2[1] - p0[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (3 * p1[1] - p0[1] - 3 * p2[1] + p3[1]) * t3),
            ))
    out.append(points[-1])
    return out


class Painter:
    """Малює на великому (суперсемпльованому) зображенні в dp-координатах.

    k = SS * масштаб екрана; (ox, oy) — зсув початку зображення в фізичних
    пікселях фінального розміру (для спрайтів, що є частиною більшої сцени).
    Товщина обведення в Tk центрується на межі фігури — Painter повторює це,
    тож координати можна брати з коду для Canvas без змін.
    """

    def __init__(self, img: Image.Image, scale: float, ox: float = 0.0, oy: float = 0.0):
        self.img = img
        self.d = ImageDraw.Draw(img)
        self.k = SS * scale
        self._ox = ox * SS
        self._oy = oy * SS

    def _p(self, x: float, y: float) -> tuple[float, float]:
        return x * self.k - self._ox, y * self.k - self._oy

    def _w(self, width: float) -> int:
        return max(1, round(width * self.k))

    def ellipse(self, x0, y0, x1, y1, fill=None, outline=None, width: float = 1) -> None:
        half = width / 2 if outline else 0
        ax, ay = self._p(x0 - half, y0 - half)
        bx, by = self._p(x1 + half, y1 + half)
        self.d.ellipse((ax, ay, bx, by), fill=fill, outline=outline, width=self._w(width) if outline else 0)

    def rect(self, x0, y0, x1, y1, fill=None, outline=None, width: float = 1) -> None:
        half = width / 2 if outline else 0
        ax, ay = self._p(x0 - half, y0 - half)
        bx, by = self._p(x1 + half, y1 + half)
        self.d.rectangle((ax, ay, bx, by), fill=fill, outline=outline, width=self._w(width) if outline else 0)

    def polygon(self, pts, fill) -> None:
        self.d.polygon([self._p(x, y) for x, y in pts], fill=fill)

    def line(self, pts, fill, width: float = 1, round_caps: bool = True) -> None:
        big = [self._p(x, y) for x, y in pts]
        w = self._w(width)
        self.d.line(big, fill=fill, width=w)
        if round_caps and w > 2:
            r = w / 2
            for x, y in (big[0], big[-1]):
                self.d.ellipse((x - r, y - r, x + r, y + r), fill=fill)

    def arc(self, x0, y0, x1, y1, start: float, extent: float, fill, width: float = 1,
            round_caps: bool = True) -> None:
        """Дуга з Tk-кутами (проти годинникової від 3 год.), bbox — по центру лінії."""
        if abs(extent) >= 359.9:
            self.ellipse(x0, y0, x1, y1, outline=fill, width=width)
            return
        half = width / 2
        ax, ay = self._p(x0 - half, y0 - half)
        bx, by = self._p(x1 + half, y1 + half)
        a0, a1 = sorted((start, start + extent))
        w = self._w(width)
        self.d.arc((ax, ay, bx, by), start=-a1, end=-a0, fill=fill, width=w)
        if round_caps and w > 2:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
            r = w / 2
            for a in (a0, a1):
                px, py = self._p(cx + rx * math.cos(math.radians(a)), cy - ry * math.sin(math.radians(a)))
                self.d.ellipse((px - r, py - r, px + r, py + r), fill=fill)


def new_layer(width: int, height: int, mode: str = "RGBA", color=(0, 0, 0, 0)) -> Image.Image:
    return Image.new(mode, (width * SS, height * SS), color)


_dot_cache: dict = {}


def dot_image(color: str, size_dp: float, bg: str, scale: float):
    """Маленька згладжена крапка (PhotoImage) — для легенди й міток; кешується."""
    from PIL import ImageTk

    key = (color, size_dp, bg, round(scale, 3))
    photo = _dot_cache.get(key)
    if photo is None:
        px = max(4, round(size_dp * scale))
        img = new_layer(px, px, "RGB", rgb(bg))
        d = ImageDraw.Draw(img)
        pad = SS * 0.5
        d.ellipse((pad, pad, px * SS - pad, px * SS - pad), fill=rgb(color))
        photo = ImageTk.PhotoImage(downscale(img, (px, px)))
        _dot_cache[key] = photo
    return photo


def glyph(kind: str, color: str, size_dp: float, scale: float) -> Image.Image:
    """Згладжена піктограма (лупа / оновлення) з прозорим тлом."""
    px = max(8, round(size_dp * scale))
    layer = new_layer(px, px, "RGBA", (0, 0, 0, 0))
    p = Painter(layer, scale)
    S = size_dp
    fill = rgb(color, 255)
    if kind == "search":
        p.ellipse(S * .14, S * .14, S * .60, S * .60, outline=fill, width=S * .11)
        p.line([(S * .56, S * .56), (S * .86, S * .86)], fill=fill, width=S * .13)
    else:  # refresh: дуга майже в коло зі стрілкою на кінці
        cx = cy = S / 2
        r = S * .34
        p.arc(cx - r, cy - r, cx + r, cy + r, start=40, extent=270, fill=fill, width=S * .11, round_caps=False)
        end = math.radians(310)
        tip_base = (cx + r * math.cos(end), cy - r * math.sin(end))
        tx, ty = -math.sin(end), -math.cos(end)  # напрям руху кінця дуги
        nx, ny = -ty, tx
        head, half = S * .24, S * .17
        p.polygon([
            (tip_base[0] + tx * head, tip_base[1] + ty * head),
            (tip_base[0] + nx * half, tip_base[1] + ny * half),
            (tip_base[0] - nx * half, tip_base[1] - ny * half),
        ], fill=fill)
    return downscale(layer, (px, px))
