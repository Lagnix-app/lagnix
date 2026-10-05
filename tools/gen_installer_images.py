"""Картинки майстра встановлення/видалення Lagnix (installer/img/*.bmp).

Робот — той самий, що в програмі (ui/widgets/robot.py), у різних настроях і з
реквізитом: веселий (вітання), з викруткою (встановлення), радий із конфеті
(фінал), сумний зі сльозинкою (видалення). Кожна картинка — у наборі розмірів
під масштаб 100-250% (Inno вибирає за DPI). Запуск: python tools/gen_installer_images.py
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

from ui.widgets import aa, robot  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "installer", "img")
BG = "#0d1321"
PANEL = "#151c2c"
PAGE_BG = "#2b2b2b"  # фон сторінок темного стилю Inno (WizardStyle=modern dark)

# розміри з документації Inno Setup для 100/125/150/175/200/225/250%
LARGE = [(164, 314), (192, 386), (246, 459), (273, 556), (328, 604), (355, 700), (410, 797)]
SMALL = [(55, 55), (64, 68), (83, 80), (92, 97), (110, 106), (119, 123), (138, 140)]

ORANGE = aa.rgb("#e0a52f")
STEEL = aa.rgb("#b8c2d6")
STEEL_DARK = aa.rgb("#8a94a6")
GREEN = aa.rgb("#2ee59d")
HANDLE = aa.rgb("#ff7a45")
BODY_BOTTOM = aa.rgb("#1f7ae0")
BODY_TOP = aa.rgb("#4fc3ff")
TEAR = aa.rgb("#7fd4ff")


def _extras(p: aa.Painter, kind: str) -> None:
    """Реквізит поверх робота (координати 0..100 квадрата робота)."""
    if kind == "screwdriver":
        # права «рука» тримає викрутку, ручка внизу, жало вгорі-праворуч
        p.line([(72, 74), (84, 84)], fill=BODY_BOTTOM, width=4.4)
        p.line([(82, 82), (93, 63)], fill=STEEL_DARK, width=2.6)       # стрижень
        p.line([(93, 63), (95.5, 58.5)], fill=STEEL, width=1.6)        # жало
        p.line([(80, 86), (86.5, 75)], fill=HANDLE, width=6.2)         # ручка
        p.ellipse(79.6, 82, 88.4, 90.8, fill=BODY_TOP, outline=BODY_BOTTOM, width=1.4)  # кулачок
        p.line([(26, 72), (21, 84)], fill=BODY_BOTTOM, width=4.4)      # ліва рука вниз
        p.ellipse(16.6, 80, 25.4, 88.8, fill=BODY_TOP, outline=BODY_BOTTOM, width=1.4)
    elif kind == "joy":
        for sx, sy, dx in ((24, 70, -10), (76, 70, 10)):               # руки вгору
            p.line([(sx, sy), (sx + dx, 52)], fill=BODY_BOTTOM, width=4.4)
            p.ellipse(sx + dx - 4.4, 44, sx + dx + 4.4, 52.8, fill=BODY_TOP, outline=BODY_BOTTOM, width=1.4)
    elif kind == "sad":
        p.ellipse(64.5, 60, 68.5, 66.5, fill=TEAR)                      # сльозинка
        p.polygon([(66.5, 55.5), (64.5, 62), (68.5, 62)], fill=TEAR)
        p.line([(26, 74), (24, 86)], fill=BODY_BOTTOM, width=4.4)       # опущені руки
        p.line([(74, 74), (76, 86)], fill=BODY_BOTTOM, width=4.4)
        p.ellipse(19.6, 83, 28.4, 91.8, fill=BODY_TOP, outline=BODY_BOTTOM, width=1.4)
        p.ellipse(71.6, 83, 80.4, 91.8, fill=BODY_TOP, outline=BODY_BOTTOM, width=1.4)


def _confetti(img: Image.Image, seed: int = 7) -> None:
    rnd = random.Random(seed)
    d = ImageDraw.Draw(img)
    w, h = img.size
    colors = [aa.rgb(c) for c in ("#2ee59d", "#e0a52f", "#ff6fa3", "#4fc3ff", "#ffffff")]
    for _ in range(46):
        x, y = rnd.uniform(0, w), rnd.uniform(0, h * 0.62)
        s = rnd.uniform(0.012, 0.024) * w
        a = rnd.uniform(0, math.tau)
        c = rnd.choice(colors)
        pts = [(x + math.cos(a + k * math.pi / 2) * s, y + math.sin(a + k * math.pi / 2) * s * 0.55)
               for k in range(4)]
        d.polygon(pts, fill=c)


def robot_image(side: int, mood: str, kind: str | None = None) -> Image.Image:
    """Робот в квадраті side x side (RGBA) з реквізитом."""
    layer = aa.new_layer(side, side)
    robot.paint_robot(layer, 0, 0, side, mood)
    if kind:
        _extras(aa.Painter(layer, side / robot.UNIT), kind)
    return aa.downscale(layer, (side, side))


def _glow(size, center, radius, color, alpha) -> Image.Image:
    layer = Image.new("RGBA", size, color + (0,))
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius),
                                 fill=alpha)
    layer.putalpha(mask.filter(ImageFilter.GaussianBlur(radius * 0.55)))
    return layer


def large(size: tuple[int, int], mood: str, kind: str | None, confetti: bool = False) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", size, aa.rgb(BG) + (255,))
    # м'яка панель знизу й зелене свічення за роботом
    ImageDraw.Draw(img).rectangle((0, round(h * 0.78), w, h), fill=aa.rgb(PANEL))
    side = round(w * 1.0)
    cy = round(h * 0.40)
    img.alpha_composite(_glow(size, (w // 2, cy), round(w * 0.46), aa.rgb("#2ee59d"), 70))
    if confetti:
        _confetti(img)
    img.alpha_composite(robot_image(side, mood, kind), (0, cy - side // 2))
    # тонка зелена «пульс»-лінія внизу, як на логотипі
    d = ImageDraw.Draw(img)
    base = round(h * 0.86)
    pts = [(x, base + math.sin(x / w * math.tau * 2) * h * 0.012) for x in range(0, w + 1, 2)]
    d.line(pts, fill=aa.rgb("#2ee59d"), width=max(1, round(h / 300)))
    return img.convert("RGB")


def small(size: tuple[int, int], mood: str, kind: str | None) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, aa.rgb(PAGE_BG))
    side = min(w, h)
    img.paste(robot_image(side, mood, kind), ((w - side) // 2, (h - side) // 2), robot_image(side, mood, kind))
    return img


def square(side: int, mood: str, kind: str | None) -> Image.Image:
    img = Image.new("RGB", (side, side), aa.rgb(PAGE_BG))
    r = robot_image(side, mood, kind)
    img.paste(r, (0, 0), r)
    return img


def save(name: str, img: Image.Image) -> None:
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    img.save(path, format="BMP")


def main() -> None:
    for w, h in LARGE:
        save(f"welcome-{w}.bmp", large((w, h), robot.HAPPY, None))
        save(f"finish-{w}.bmp", large((w, h), robot.HAPPY, "joy", confetti=True))
        save(f"uninstall-{w}.bmp", large((w, h), robot.SAD, "sad"))
    for w, h in SMALL:
        save(f"small-{w}.bmp", small((w, h), robot.HAPPY, None))
    for side in (160, 240, 320):
        save(f"installing-{side}.bmp", square(side, robot.CALM, "screwdriver"))
        save(f"sad-{side}.bmp", square(side, robot.SAD, "sad"))
    print("OK:", OUT)


if __name__ == "__main__":
    main()
