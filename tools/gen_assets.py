"""Генерує лого/іконку PulseFPS у assets/ (робот + PNG різних розмірів + .ico).

Малює напряму через Pillow (без cairosvg, якого немає в оточенні) з 4x
supersampling для згладжування, тож і 16-піксельна іконка виходить чіткою.
Також малює прапорці мов (assets/flags/, через ui/widgets/flags.py).
Запуск: python tools/gen_assets.py — перезаписує файли в assets/.
"""

import math
import os
import sys

from PIL import Image, ImageDraw

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

BODY_TOP = (79, 195, 255)      # #4fc3ff
BODY_BOTTOM = (31, 122, 224)   # #1f7ae0
SCREEN_BG = (13, 19, 33)       # #0d1321
EYE_GREEN = (46, 229, 157)     # #2ee59d
ANTENNA_STICK = (138, 148, 166)
BROOM_HANDLE = (138, 90, 43)
BROOM_HEAD = (224, 165, 47)
PULSE_LINE = (46, 229, 157)

SS = 4  # supersampling factor


def _vertical_gradient(size, top, bottom):
    w, h = size
    grad = Image.new("RGB", (1, h), color=0)
    for y in range(h):
        t = y / max(h - 1, 1)
        r = round(top[0] + (bottom[0] - top[0]) * t)
        g = round(top[1] + (bottom[1] - top[1]) * t)
        b = round(top[2] + (bottom[2] - top[2]) * t)
        grad.putpixel((0, y), (r, g, b))
    return grad.resize((w, h))


def draw_icon(px: int) -> Image.Image:
    s = px * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx, cy = s / 2, s * 0.56
    radius = s * 0.36

    # антена (за тілом): стрижень + світло, що трохи виступає над колом
    ant_top = (cx, cy - radius - s * 0.16)
    draw.line([(cx, cy - radius * 0.9), ant_top], fill=ANTENNA_STICK + (255,), width=max(1, round(s * 0.018)))
    glow_r = s * 0.09
    for i in range(5, 0, -1):
        alpha = int(70 * (i / 5))
        r = glow_r * (i / 5)
        draw.ellipse(
            [ant_top[0] - r, ant_top[1] - r, ant_top[0] + r, ant_top[1] + r],
            fill=EYE_GREEN + (alpha,),
        )
    core_r = s * 0.035
    draw.ellipse(
        [ant_top[0] - core_r, ant_top[1] - core_r, ant_top[0] + core_r, ant_top[1] + core_r],
        fill=EYE_GREEN + (255,),
    )

    # мітла за тілом, трохи виглядає праворуч-знизу
    handle_base = (cx + radius * 0.5, cy + radius * 0.65)
    handle_tip = (cx + radius * 1.05, cy + radius * 1.12)
    draw.line([handle_base, handle_tip], fill=BROOM_HANDLE + (255,), width=max(1, round(s * 0.024)))
    perp = math.atan2(handle_tip[1] - handle_base[1], handle_tip[0] - handle_base[0]) + math.pi / 2
    spread = s * 0.06
    p1 = (handle_tip[0] + math.cos(perp) * spread, handle_tip[1] + math.sin(perp) * spread)
    p2 = (handle_tip[0] - math.cos(perp) * spread, handle_tip[1] - math.sin(perp) * spread)
    tip_ext = (handle_tip[0] + (handle_tip[0] - handle_base[0]) * 0.32, handle_tip[1] + (handle_tip[1] - handle_base[1]) * 0.32)
    draw.polygon([handle_tip, p1, tip_ext, p2], fill=BROOM_HEAD + (255,))

    # кругле тіло з вертикальним градієнтом
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=255)
    body = _vertical_gradient((s, s), BODY_TOP, BODY_BOTTOM).convert("RGBA")
    img.paste(body, (0, 0), mask)
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        outline=BODY_BOTTOM + (255,), width=max(1, round(s * 0.012)),
    )

    # темний екран-обличчя
    panel_w, panel_h = radius * 0.95, radius * 0.7
    px0, py0 = cx - panel_w / 2, cy - panel_h / 2
    px1, py1 = cx + panel_w / 2, cy + panel_h / 2
    draw.rounded_rectangle([px0, py0, px1, py1], radius=panel_h * 0.28, fill=SCREEN_BG + (255,))

    # очі-дужки (зелені арки)
    eye_w, eye_h = panel_w * 0.22, panel_h * 0.62
    eye_y = cy - eye_h * 0.1
    lw = max(1, round(s * 0.02))
    for side in (-1, 1):
        ex = cx + side * panel_w * 0.26
        bbox = [ex - eye_w / 2, eye_y - eye_h / 2, ex + eye_w / 2, eye_y + eye_h / 2]
        if side < 0:
            draw.arc(bbox, start=110, end=250, fill=EYE_GREEN + (255,), width=lw)
        else:
            draw.arc(bbox, start=-70, end=70, fill=EYE_GREEN + (255,), width=lw)

    img = img.resize((px, px), Image.LANCZOS)
    return img


def draw_logo_banner(width: int = 512, height: int = 160) -> Image.Image:
    """Горизонтальний банер (робот + текстове лого) — для README/маркетингу,
    сам сайдбар малює текст живими CTkLabel, це лише статичний PNG-referens."""
    s = 4
    img = Image.new("RGBA", (width * s, height * s), (13, 19, 33, 255))
    draw = ImageDraw.Draw(img)
    for x in range(0, width * s, 6):
        t = (math.sin(x / (width * s) * math.pi * 2) + 1) / 2
        color = tuple(round(PULSE_LINE[i] * 0.5) for i in range(3))
        y = int(height * s * 0.85 - t * height * s * 0.06)
        draw.ellipse([x, y - 2, x + 2, y + 2], fill=color + (140,))
    icon = draw_icon(height - 20).resize(((height - 20) * s, (height - 20) * s), Image.LANCZOS)
    img.paste(icon, (20 * s, 10 * s), icon)
    img = img.resize((width, height), Image.LANCZOS)
    return img


def main() -> None:
    os.makedirs(ASSETS, exist_ok=True)

    sizes = (16, 32, 48, 256)
    images = {size: draw_icon(size) for size in sizes}

    for size, image in images.items():
        image.save(os.path.join(ASSETS, f"pulsefps-icon-{size}.png"))

    images[256].save(
        os.path.join(ASSETS, "pulsefps.ico"),
        sizes=[(s, s) for s in sizes],
    )

    banner = draw_logo_banner()
    banner.save(os.path.join(ASSETS, "pulsefps-logo.png"))

    # прапорці мов для вибору мови: assets/flags/<код>.png (20x14) і <код>@2x.png
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ui.widgets import flags
    flags.ensure_flags(force=True)

    print("Done:", ", ".join(sorted(os.listdir(ASSETS))))


if __name__ == "__main__":
    main()
