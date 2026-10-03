"""Прапорці мов для вибору мови інтерфейсу: PNG 20x14 (і 40x28 для екранів зі
збільшенням), намальовані кодом через Pillow, зі злегка заокругленими кутами й
тонкою сірою рамкою (щоб білі смуги не зливались із фоном).

Файли — assets/flags/<код>.png і <код>@2x.png. Генеруються один раз
(ensure_flags), потім лише читаються; tools/gen_assets.py перемальовує їх
примусово. Малювання — у 4 рази більшому розмірі й зменшення з LANCZOS
(згладжені краї зірок, кіл і діагоналей).
"""

from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw

from core.i18n import LANGUAGE_CODES

FLAGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "assets", "flags")
SIZE = (20, 14)
_SUPERSAMPLE = 4
_BORDER = (150, 158, 172, 255)  # тонка сіра рамка


def flag_path(code: str, scale: int = 1) -> str:
    return os.path.join(FLAGS_DIR, f"{code}.png" if scale == 1 else f"{code}@{scale}x.png")


def ensure_flags(force: bool = False) -> None:
    """Створює відсутні файли прапорців (force — перемалювати всі)."""
    os.makedirs(FLAGS_DIR, exist_ok=True)
    for code in LANGUAGE_CODES:
        for scale in (1, 2):
            path = flag_path(code, scale)
            if force or not os.path.exists(path):
                render_flag(code, scale).save(path)


def render_flag(code: str, scale: int = 1) -> Image.Image:
    w, h = SIZE[0] * scale, SIZE[1] * scale
    big_w, big_h = w * _SUPERSAMPLE, h * _SUPERSAMPLE
    img = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _DRAWERS[code](draw, big_w, big_h)

    # злегка заокруглені кути: ~1.6 px при 20x14
    radius = round(1.6 * scale * _SUPERSAMPLE)
    mask = Image.new("L", (big_w, big_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big_w - 1, big_h - 1), radius=radius, fill=255)
    rounded = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
    rounded.paste(img, (0, 0), mask)
    border = max(1, round(0.8 * scale * _SUPERSAMPLE))
    ImageDraw.Draw(rounded).rounded_rectangle((0, 0, big_w - 1, big_h - 1), radius=radius,
                                              outline=_BORDER, width=border)
    return rounded.resize((w, h), Image.LANCZOS)


# ------------------------------------------------------------- малювання

def _hstripes(draw, w, h, colors, weights=None) -> None:
    weights = weights or [1] * len(colors)
    total, y = sum(weights), 0.0
    for color, weight in zip(colors, weights):
        y2 = y + h * weight / total
        draw.rectangle((0, round(y), w, round(y2)), fill=color)
        y = y2


def _vstripes(draw, w, h, colors) -> None:
    for i, color in enumerate(colors):
        draw.rectangle((round(w * i / len(colors)), 0, round(w * (i + 1) / len(colors)), h), fill=color)


def _star(draw, cx, cy, r, color, rotation=-math.pi / 2) -> None:
    """П'ятикутна зірка; rotation — кут першого променя (типово — вгору)."""
    points = []
    for i in range(10):
        radius = r if i % 2 == 0 else r * 0.382
        angle = rotation + i * math.pi / 5
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    draw.polygon(points, fill=color)


def _circle(draw, cx, cy, r, color) -> None:
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)


def _uk(draw, w, h):
    _hstripes(draw, w, h, ("#0057B7", "#FFD700"))


def _en(draw, w, h):
    """Прапор Великої Британії (Union Jack)."""
    draw.rectangle((0, 0, w, h), fill="#012169")
    white_diag, red_diag = h * 0.2, h * 0.08
    for (x0, y0, x1, y1) in ((0, 0, w, h), (0, h, w, 0)):
        draw.line((x0, y0, x1, y1), fill="#FFFFFF", width=round(white_diag))
        draw.line((x0, y0, x1, y1), fill="#C8102E", width=round(red_diag))
    cross_white, cross_red = h * 0.34, h * 0.2
    draw.rectangle((w / 2 - cross_white / 2, 0, w / 2 + cross_white / 2, h), fill="#FFFFFF")
    draw.rectangle((0, h / 2 - cross_white / 2, w, h / 2 + cross_white / 2), fill="#FFFFFF")
    draw.rectangle((w / 2 - cross_red / 2, 0, w / 2 + cross_red / 2, h), fill="#C8102E")
    draw.rectangle((0, h / 2 - cross_red / 2, w, h / 2 + cross_red / 2), fill="#C8102E")


def _pl(draw, w, h):
    _hstripes(draw, w, h, ("#FFFFFF", "#DC143C"))


def _de(draw, w, h):
    _hstripes(draw, w, h, ("#000000", "#DD0000", "#FFCE00"))


def _es(draw, w, h):
    _hstripes(draw, w, h, ("#AA151B", "#F1BF00", "#AA151B"), (1, 2, 1))


def _pt_br(draw, w, h):
    draw.rectangle((0, 0, w, h), fill="#009C3B")
    mx, my = w * 0.085, h * 0.12
    draw.polygon(((mx, h / 2), (w / 2, my), (w - mx, h / 2), (w / 2, h - my)), fill="#FFDF00")
    r = h * 0.25
    _circle(draw, w / 2, h / 2, r, "#002776")
    # біла стрічка через синє коло
    draw.arc((w / 2 - r * 1.9, h / 2 - r * 0.55, w / 2 + r * 1.1, h / 2 + r * 2.3), start=205, end=300,
             fill="#FFFFFF", width=max(1, round(h * 0.035)))


def _fr(draw, w, h):
    _vstripes(draw, w, h, ("#0055A4", "#FFFFFF", "#EF4135"))


def _tr(draw, w, h):
    draw.rectangle((0, 0, w, h), fill="#E30A17")
    _circle(draw, w * 0.37, h / 2, h * 0.25, "#FFFFFF")
    _circle(draw, w * 0.41, h / 2, h * 0.2, "#E30A17")
    _star(draw, w * 0.56, h / 2, h * 0.125, "#FFFFFF", rotation=math.pi)


def _ru(draw, w, h):
    # біло-синьо-білий: три рівні горизонтальні смуги
    _hstripes(draw, w, h, ("#FFFFFF", "#0055A4", "#FFFFFF"))


def _zh_cn(draw, w, h):
    draw.rectangle((0, 0, w, h), fill="#DE2910")
    ux, uy = w / 30, h / 20
    _star(draw, 5 * ux, 5 * uy, 3 * uy, "#FFDE00")
    for sx, sy in ((10, 2), (12, 4), (12, 7), (10, 9)):
        angle = math.atan2(5 - sy, 5 - sx)  # малі зірки «дивляться» на велику
        _star(draw, sx * ux, sy * uy, 1.1 * uy, "#FFDE00", rotation=angle)


def _ja(draw, w, h):
    draw.rectangle((0, 0, w, h), fill="#FFFFFF")
    _circle(draw, w / 2, h / 2, h * 0.3, "#BC002D")


def _ko(draw, w, h):
    draw.rectangle((0, 0, w, h), fill="#FFFFFF")
    cx, cy, r = w / 2, h / 2, h * 0.25
    _circle(draw, cx, cy, r, "#0047A0")
    draw.pieslice((cx - r, cy - r, cx + r, cy + r), 180, 360, fill="#CD2E3A")
    _circle(draw, cx - r / 2, cy, r / 2, "#CD2E3A")
    _circle(draw, cx + r / 2, cy, r / 2, "#0047A0")
    # чотири триграми — короткі чорні риски по діагоналях
    bar_len, bar_w, gap = h * 0.17, max(1, round(h * 0.035)), h * 0.055
    for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        ox, oy = cx + sx * w * 0.3, cy + sy * h * 0.3
        angle = math.atan2(sy * h, sx * w) + math.pi / 2  # риски перпендикулярні діагоналі
        dx, dy = math.cos(angle) * bar_len / 2, math.sin(angle) * bar_len / 2
        nx, ny = math.cos(angle - math.pi / 2) * gap, math.sin(angle - math.pi / 2) * gap
        for k in (-1, 0, 1):
            px, py = ox + nx * k, oy + ny * k
            draw.line((px - dx, py - dy, px + dx, py + dy), fill="#000000", width=bar_w)


_DRAWERS = {
    "uk": _uk, "en": _en, "pl": _pl, "de": _de, "es": _es, "pt-BR": _pt_br, "fr": _fr,
    "tr": _tr, "ru": _ru, "zh-CN": _zh_cn, "ja": _ja, "ko": _ko,
}
