"""Монтаж промо-відео Lagnix: кадри з `python main.py --promo <сценарій>:<мова>` -> 1080x1920, 60 к/с, MP4 H.264.

Що додає монтаж до запису вікна: фон у стилі Lagnix (#0d1321, блакитний #4fc3ff, зелений #2ee59d), тінь і
рамку вікна, субтитри (білі з темним обведенням, анімована поява), плавний намальований курсор із
кліком, фінал із заставкою (робот махає рукою + «Lagnix»). Музики немає.

Залежності лише для монтажу (не входять у requirements.txt програми): pip install mss numpy imageio-ffmpeg

  python tools/promo_render.py stills <sad_robot|one_click> <en|uk>   # 5 ключових кадрів у promo/preview/
  python tools/promo_render.py render <sad_robot|one_click> <en|uk>   # повне відео в promo/
  python tools/promo_render.py intro                                  # заставка promo/intro.mp4
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

W, H, FPS = 1080, 1920, 60
OUT_DIR = os.path.join(ROOT, "promo")
WORK_DIR = os.path.join(OUT_DIR, "_work")
PREVIEW_DIR = os.path.join(OUT_DIR, "preview")

BG = (13, 19, 33)
BLUE = (79, 195, 255)
GREEN = (46, 229, 157)
WHITE = (255, 255, 255)
STROKE = (8, 12, 24)
FONT_BOLD = r"C:\Windows\Fonts\segoeuib.ttf"
FONT_EMOJI = r"C:\Windows\Fonts\seguiemj.ttf"

OUTRO_S = 3.0
INTRO_S = 2.0
WINDOW_FADE_S = 0.45
OUTPUT_NAMES = {"sad_robot": "sad-robot", "one_click": "one-click"}

CAPTIONS = {
    "en": {"uninstall_title": "I tried to uninstall my own app…", "dots": "…",
           "ram_full": "RAM full before a match? 😩", "one_click": "One click before you play",
           "safe": "Safe for Valorant & CS2 — no injection, full backups"},
    "uk": {"uninstall_title": "Я спробував видалити свою програму…", "dots": "…",
           "ram_full": "RAM забита перед грою? 😩", "one_click": "Одна кнопка перед грою",
           "safe": "Безпечно для Valorant і CS2 — без інжектів, з бекапами"},
}
TAGLINES = {"en": ("Free FPS booster for Windows", "Link in bio"),
            "uk": ("Безкоштовний FPS booster", "Посилання в профілі")}


# ------------------------------------------------------------------ допоміжне

def ease_out_back(k: float, s: float = 1.9) -> float:
    k = min(max(k, 0.0), 1.0)
    return 1 + (s + 1) * (k - 1) ** 3 + s * (k - 1) ** 2


def ease_out(k: float) -> float:
    return 1 - (1 - min(max(k, 0.0), 1.0)) ** 3


def smoother(k: float) -> float:
    k = min(max(k, 0.0), 1.0)
    return k * k * k * (k * (6 * k - 15) + 10)


_fonts: dict = {}


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(path, size)
    return _fonts[key]


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return cp >= 0x1F000 or 0x2600 <= cp <= 0x27BF and ch not in "—"


def _runs(text: str):
    run, emoji = "", False
    for ch in text:
        e = _is_emoji(ch)
        if run and e != emoji:
            yield run, emoji
            run = ""
        run, emoji = run + ch, e
    if run:
        yield run, emoji


def text_width(text: str, size: int) -> float:
    total = 0.0
    for run, emoji in _runs(text):
        total += font(FONT_EMOJI if emoji else FONT_BOLD, size).getlength(run)
    return total


def wrap(text: str, size: int, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split(" "):
        trial = (cur + " " + word).strip()
        if cur and text_width(trial, size) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    lines.append(cur)
    return lines


def draw_text_line(img: Image.Image, cx: float, y: float, text: str, size: int, fill=WHITE, stroke=0,
                   stroke_fill=STROKE, anchor_x="center") -> None:
    """Рядок тексту: звичайні фрагменти — Segoe UI Bold з обведенням, емодзі — кольорові (Segoe UI Emoji)."""
    width = text_width(text, size)
    x = cx - width / 2 if anchor_x == "center" else cx
    d = ImageDraw.Draw(img)
    for run, emoji in _runs(text):
        if emoji:
            f = font(FONT_EMOJI, size)
            d.text((x, y + size * 0.05), run, font=f, embedded_color=True)
        else:
            f = font(FONT_BOLD, size)
            d.text((x, y), run, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
        x += font(FONT_EMOJI if emoji else FONT_BOLD, size).getlength(run)


def caption_image(text: str, max_size: int, max_w: int = 940, max_lines: int = 3) -> Image.Image:
    """Готовий RGBA-малюнок субтитру (білий текст, темне обведення), по центру."""
    size = max_size
    while True:
        lines = wrap(text, size, max_w)
        if len(lines) <= max_lines or size <= 54:
            break
        size -= 4
    stroke = max(7, size // 10)
    line_h = int(size * 1.28)
    pad = stroke * 2 + 6
    img = Image.new("RGBA", (W, line_h * len(lines) + pad * 2), (0, 0, 0, 0))
    for i, line in enumerate(lines):
        draw_text_line(img, W / 2, pad + i * line_h, line, size, WHITE, stroke)
    box = img.getbbox()
    return img.crop((0, box[1] - 4, W, box[3] + 4)) if box else img


# ------------------------------------------------------------------ фон і вікно

_bg_cache: Image.Image | None = None


def background() -> Image.Image:
    global _bg_cache
    if _bg_cache is None:
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        base = np.zeros((H, W, 3), np.float32) + np.array(BG, np.float32)
        for (cx, cy, r, col, a) in ((W * 0.95, H * 0.12, 900, BLUE, 0.20), (W * 0.05, H * 0.9, 1000, GREEN, 0.15),
                                    (W * 0.5, H * 0.5, 1400, (30, 60, 110), 0.10)):
            d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r
            k = np.clip(1 - d, 0, 1) ** 2 * a
            base += (np.array(col, np.float32) - base) * k[..., None]
        vignette = 1 - 0.28 * np.clip(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2 - 0.4, 0, 1)
        base *= vignette[..., None]
        _bg_cache = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    return _bg_cache


class WindowLayer:
    """Вікно програми на канвасі: заокруглення, рамка, тінь (готується один раз)."""

    def __init__(self, size: tuple[int, int], scale: float, crop_bottom: int = 0):
        self.src_size = (size[0], size[1] - crop_bottom)
        self.scale = scale
        self.w = round(self.src_size[0] * scale)
        self.h = round(self.src_size[1] * scale)
        self.x = (W - self.w) // 2
        self.y = 640 if scale == 1.0 else 690
        radius = 22
        self.mask = Image.new("L", (self.w, self.h), 0)
        ImageDraw.Draw(self.mask).rounded_rectangle((0, 0, self.w - 1, self.h - 1), radius, fill=255)
        pad = 70
        shadow = Image.new("L", (self.w + pad * 2, self.h + pad * 2), 0)
        ImageDraw.Draw(shadow).rounded_rectangle((pad, pad + 18, pad + self.w, pad + 18 + self.h), radius, fill=170)
        self.shadow = shadow.filter(ImageFilter.GaussianBlur(34))
        self.shadow_pad = pad
        border = Image.new("L", (self.w, self.h), 0)
        ImageDraw.Draw(border).rounded_rectangle((0, 0, self.w - 1, self.h - 1), radius, outline=255, width=2)
        self.border = border

    def to_canvas(self, x: float, y: float) -> tuple[float, float]:
        return self.x + x * self.scale, self.y + y * self.scale

    def draw(self, canvas: Image.Image, frame: Image.Image, alpha: float, grow: float = 1.0) -> None:
        if alpha <= 0.001:
            return
        frame = frame.crop((0, 0) + self.src_size)
        if self.scale != 1.0:
            frame = frame.resize((self.w, self.h), Image.Resampling.LANCZOS)
        shadow, mask, border = self.shadow, self.mask, self.border
        x, y, w, h = self.x, self.y, self.w, self.h
        if grow != 1.0:
            nw, nh = round(w * grow), round(h * grow)
            frame = frame.resize((nw, nh), Image.Resampling.BILINEAR)
            mask = mask.resize((nw, nh), Image.Resampling.BILINEAR)
            border = border.resize((nw, nh), Image.Resampling.BILINEAR)
            x, y = x + (w - nw) // 2, y + (h - nh) // 2
            shadow = shadow.resize((round(shadow.width * grow), round(shadow.height * grow)), Image.Resampling.BILINEAR)
        sx, sy = x - self.shadow_pad * (shadow.width / self.shadow.width), y - self.shadow_pad * (shadow.height / self.shadow.height)
        canvas.paste((0, 0, 0), (round(sx), round(sy)), shadow.point(lambda v: int(v * alpha)))
        canvas.paste(frame, (x, y), mask.point(lambda v: int(v * alpha)))
        canvas.paste((35, 45, 66), (x, y), border.point(lambda v: int(v * alpha)))


# ------------------------------------------------------------------ курсор

def _cursor_sprite() -> Image.Image:
    s = 4
    base = Image.new("RGBA", (34 * s, 46 * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    pts = [(3, 2), (3, 33), (11, 26), (17, 40), (23, 37.5), (17, 24), (28, 24)]
    pts = [(x * s, y * s) for x, y in pts]
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).polygon([(x + 3 * s, y + 4 * s) for x, y in pts], fill=(0, 0, 0, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(3 * s))
    base.alpha_composite(shadow)
    d.polygon(pts, fill=(255, 255, 255, 255), outline=(10, 14, 24, 255), width=2 * s)
    return base.resize((34, 46), Image.Resampling.LANCZOS)


_cursor = None


def cursor_pos(segments: list, t: float) -> tuple[float, float] | None:
    if not segments:
        return None
    pos = (segments[0][2], segments[0][3])
    for t0, t1, x0, y0, x1, y1, bend in segments:
        if t < t0:
            break
        k = (t - t0) / max(t1 - t0, 1e-6)
        if k >= 1:
            pos = (x1, y1)
            continue
        e = smoother(k)
        dx, dy = x1 - x0, y1 - y0
        dist = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / dist, dx / dist
        off = bend * math.sin(math.pi * e)
        return x0 + dx * e + nx * off, y0 + dy * e + ny * off
    return pos


def draw_cursor(canvas: Image.Image, pos: tuple[float, float], t: float, clicks: list[float]) -> None:
    global _cursor
    if _cursor is None:
        _cursor = _cursor_sprite()
    x, y = pos
    press = 0.0
    for c in clicks:
        age = t - c
        if -0.05 <= age < 0.45:
            # кільце кліку
            k = max(age, 0) / 0.45
            r = 10 + 46 * ease_out(k)
            ring = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            ImageDraw.Draw(ring).ellipse((x - r, y - r, x + r, y + r), outline=BLUE + (int(220 * (1 - k)),), width=4)
            canvas.paste(ring, (0, 0), ring)
        if 0 <= age < 0.14:
            press = 1 - abs(age - 0.07) / 0.07
    sprite = _cursor
    if press > 0:
        k = 1 - 0.12 * press
        sprite = sprite.resize((round(34 * k), round(46 * k)), Image.Resampling.LANCZOS)
    canvas.paste(sprite, (round(x - 3), round(y - 2)), sprite)


# ------------------------------------------------------------------ заставка

_robot_cache: dict = {}


def robot_sprite(side: int, mood: str = "happy") -> Image.Image:
    key = (side, mood)
    if key not in _robot_cache:
        from ui.widgets import robot as robot_view
        _robot_cache[key] = robot_view.render_robot(side, mood).convert("RGBA")
    return _robot_cache[key]


def robot_with_arm(side: int, t: float, wave: float) -> Image.Image:
    """Робот із рукою, що махає: wave 0..1 — наскільки піднята рука (0 — опущена, схована за тілом)."""
    pad = side // 2
    size = side + pad * 2
    ss = 3
    layer = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    u = side / 100 * ss
    ox = oy = pad * ss
    if wave > 0.01:
        swing = math.sin(t * 2 * math.pi * 2.3) * 24 * wave
        angle = math.radians(58 + swing + (1 - wave) * -40)
        sx, sy = 78 * u + ox, 68 * u + oy
        length = 27 * u * (0.5 + 0.5 * wave)
        hx, hy = sx + math.cos(angle) * length, sy - math.sin(angle) * length
        d.line([(sx, sy), (hx, hy)], fill=(31, 122, 224, 255), width=round(11 * u), joint="curve")
        for p in ((sx, sy), (hx, hy)):
            r = 5.5 * u
            d.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=(31, 122, 224, 255))
        r = 9 * u
        d.ellipse((hx - r, hy - r, hx + r, hy + r), fill=(79, 195, 255, 255), outline=(31, 122, 224, 255),
                  width=round(2.2 * u))
    arm = layer.resize((size, size), Image.Resampling.LANCZOS)
    arm.alpha_composite(robot_sprite(side), (pad, pad))
    return arm


_gradient_text_cache: dict = {}


def lagnix_text(size: int) -> Image.Image:
    """Напис «Lagnix» із градієнтом зелений -> блакитний."""
    if size not in _gradient_text_cache:
        f = font(FONT_BOLD, size)
        w = int(f.getlength("Lagnix")) + 20
        h = int(size * 1.4)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).text((10, size * 0.08), "Lagnix", font=f, fill=255)
        grad = Image.new("RGB", (w, h))
        gd = ImageDraw.Draw(grad)
        for x in range(w):
            k = x / max(w - 1, 1)
            gd.line([(x, 0), (x, h)], fill=tuple(round(GREEN[i] + (BLUE[i] - GREEN[i]) * k) for i in range(3)))
        img = grad.convert("RGBA")
        img.putalpha(mask)
        _gradient_text_cache[size] = img.crop(img.getbbox())
    return _gradient_text_cache[size]


ROBOT_SIDE = 330
TEXT_SIZE = 168
LOCKUP_Y = 800         # центр заставки по вертикалі


def draw_intro(canvas: Image.Image, t: float, lang: str | None = None) -> None:
    """Заставка: робот з'являється (пружинить), махає рукою, поруч виїжджає «Lagnix». t — секунди від початку.
    З lang додатково з'являються рядки під заставкою (фінал відео)."""
    text = lagnix_text(TEXT_SIZE)
    gap = 78
    total = ROBOT_SIDE * 0.86 + gap + text.width
    left = (W - total) / 2
    robot_final_cx = left + ROBOT_SIDE * 0.43
    text_x = left + ROBOT_SIDE * 0.86 + gap

    appear = ease_out_back((t - 0.05) / 0.5)
    slide = smoother((t - 0.95) / 0.7)
    cx = W / 2 + (robot_final_cx - W / 2) * slide
    bob = math.sin(t * 2 * math.pi / 1.6) * 5
    cy = LOCKUP_Y + bob
    wave = smoother((t - 0.35) / 0.3)
    side = max(int(ROBOT_SIDE * max(appear, 0.01)), 8)
    sprite = robot_with_arm(ROBOT_SIDE, t, wave)
    if side != ROBOT_SIDE:
        k = max(appear, 0.01)
        sprite = sprite.resize((max(8, round(sprite.width * k)), max(8, round(sprite.height * k))), Image.Resampling.LANCZOS)
    # напис виїжджає з-за робота
    k_text = ease_out(slide)
    if k_text > 0.01:
        dx = (1 - k_text) * -120
        layer = text.copy()
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * min(1, k_text * 1.6))))
        canvas.paste(layer, (round(text_x + dx), round(LOCKUP_Y - text.height / 2 + 10)), layer)
    # м'яке світіння під роботом
    glow = Image.new("RGBA", (sprite.width + 160, sprite.height + 160), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((60, 60, glow.width - 60, glow.height - 60), fill=GREEN + (34,))
    glow = glow.filter(ImageFilter.GaussianBlur(40))
    canvas.paste(glow, (round(cx - glow.width / 2), round(cy - glow.height / 2)), glow)
    canvas.paste(sprite, (round(cx - sprite.width / 2), round(cy - sprite.height / 2)), sprite)

    if lang:
        line1, line2 = TAGLINES[lang]
        a1 = ease_out((t - 1.45) / 0.45)
        a2 = ease_out((t - 1.85) / 0.45)
        if a1 > 0.01:
            img = Image.new("RGBA", (W, 130), (0, 0, 0, 0))
            size = 62 if text_width(line1, 62) < 960 else 54
            draw_text_line(img, W / 2, 16, line1, size, WHITE, 6)
            img.putalpha(img.getchannel("A").point(lambda v: int(v * a1)))
            canvas.paste(img, (0, round(1100 + (1 - a1) * 26)), img)
        if a2 > 0.01:
            f = font(FONT_BOLD, 54)
            tw = f.getlength(line2)
            pw, ph = int(tw + 110), 100
            pill = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
            pd = ImageDraw.Draw(pill)
            pd.rounded_rectangle((0, 0, pw - 1, ph - 1), 50, fill=GREEN + (255,))
            pd.text((pw / 2, ph / 2 - 2), line2, font=f, fill=BG, anchor="mm")
            pill.putalpha(pill.getchannel("A").point(lambda v: int(v * a2)))
            canvas.paste(pill, (round((W - pw) / 2), round(1262 + (1 - a2) * 26)), pill)


# ------------------------------------------------------------------ композиція відео

class Video:
    def __init__(self, scenario: str, lang: str):
        self.scenario, self.lang = scenario, lang
        self.dir = os.path.join(WORK_DIR, f"{scenario}_{lang}")
        with open(os.path.join(self.dir, "timeline.json"), encoding="utf-8") as f:
            self.tl = json.load(f)
        layout = self.tl["layout"]
        self.window = WindowLayer(tuple(self.tl["size"]), layout["scale"], crop_bottom=2 if layout["mode"] == "dialog" else 0)
        self.outro_at = self.tl["cues"]["outro"]
        self.duration = self.outro_at + OUTRO_S
        self.captions = self.tl["captions"]
        self.show_at = self.captions[0][1] if self.captions else 0.3   # вікно з'являється після першого титру
        self.frame_times = self.tl["frames"]
        self._last = (-1, None)
        self._caps: dict = {}

    @property
    def total_frames(self) -> int:
        return int(round(self.duration * FPS))

    def source_frame(self, t: float) -> Image.Image:
        times = self.frame_times
        lo, hi = 0, len(times) - 1
        while lo < hi:   # останній кадр з часом <= t
            mid = (lo + hi + 1) // 2
            if times[mid] <= t + 0.004:
                lo = mid
            else:
                hi = mid - 1
        if self._last[0] != lo:
            self._last = (lo, Image.open(os.path.join(self.dir, "frames", f"{lo:05d}.jpg")).convert("RGB"))
        return self._last[1]

    def caption(self, index: int, key: str) -> Image.Image:
        if index not in self._caps:
            big = index == 0
            size = 300 if key == "dots" else (112 if big else 88)
            self._caps[index] = caption_image(CAPTIONS[self.lang][key], size, max_lines=4 if big else 3)
        return self._caps[index]

    def render(self, frame_index: int) -> Image.Image:
        t = frame_index / FPS
        canvas = background().copy()
        if t >= self.outro_at:
            draw_intro(canvas, t - self.outro_at, self.lang)
            return canvas
        # ---- вікно
        fade_in = ease_out((t - self.show_at) / WINDOW_FADE_S)
        fade_out = 1 - smoother((t - (self.outro_at - 0.3)) / 0.3)
        alpha = fade_in * fade_out
        if alpha > 0:
            grow = 0.94 + 0.06 * ease_out_back((t - self.show_at) / 0.6, 1.3) if t < self.show_at + 0.7 else 1.0
            self.window.draw(canvas, self.source_frame(t), alpha, grow)
        # ---- субтитри
        for i, (t0, t1, key) in enumerate(self.captions):
            if not (t0 - 0.01 <= t <= t1 + 0.25):
                continue
            img = self.caption(i, key)
            k_in = ease_out_back((t - t0) / 0.32)
            k_out = 1 - smoother((t - t1) / 0.2)
            a = min(1.0, max(0.0, (t - t0) / 0.18)) * k_out
            if a <= 0.01:
                continue
            scale = (0.86 + 0.14 * k_in) * (1 + 0.04 * (1 - k_out))
            if abs(scale - 1) > 0.002:
                img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.Resampling.LANCZOS)
            img = img.copy()
            img.putalpha(img.getchannel("A").point(lambda v: int(v * a)))
            center_y = 900 if i == 0 else 395
            y = center_y - img.height / 2 + (1 - min(k_in, 1)) * 36
            canvas.paste(img, (round((W - img.width) / 2), round(y)), img)
        # ---- курсор
        vis = False
        for tv, v in self.tl["cursor_vis"]:
            if tv <= t:
                vis = v
        pos = cursor_pos(self.tl["cursor"], t)
        if vis and pos and alpha > 0.2:
            cx, cy = self.window.to_canvas(*pos)
            if -40 < cx < W + 40 and -40 < cy < H + 40:
                draw_cursor(canvas, (cx, cy), t, [self.window_click(c) for c in self.tl["clicks"]])
        return canvas

    def window_click(self, c: float) -> float:
        return c


def key_times(video: Video) -> list[float]:
    cues, caps = video.tl["cues"], video.captions
    if video.scenario == "sad_robot":
        return [caps[0][0] + 0.9, cues["at_yes"] - 1.8, cues["at_yes"] + 1.2, cues["happy"] + 0.7, video.duration - 0.2]
    return [caps[0][0] + 0.9, cues["processes"] + 1.3, cues["game_tab"] + 1.2,
            cues["monitor_again"] + 2.5, video.duration - 0.2]


def encode(frames, out_path: str) -> None:
    import imageio_ffmpeg
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "16",
           "-pix_fmt", "yuv420p", "-profile:v", "high", "-r", str(FPS), "-movflags", "+faststart", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for img in frames:
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("ffmpeg завершився з помилкою")


def cmd_stills(scenario: str, lang: str) -> None:
    video = Video(scenario, lang)
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    name = OUTPUT_NAMES[scenario]
    for n, t in enumerate(key_times(video), 1):
        path = os.path.join(PREVIEW_DIR, f"{name}_{lang}_{n}.png")
        video.render(min(int(round(t * FPS)), video.total_frames - 1)).save(path)
        print(path, f"t={t:.2f}")


def cmd_render(scenario: str, lang: str) -> None:
    video = Video(scenario, lang)
    out = os.path.join(OUT_DIR, f"{OUTPUT_NAMES[scenario]}_{lang}.mp4")
    encode((video.render(i) for i in range(video.total_frames)), out)
    print(out, f"{video.duration:.1f} с")


def cmd_intro() -> None:
    def frames():
        for i in range(int(INTRO_S * FPS)):
            canvas = background().copy()
            draw_intro(canvas, i / FPS)
            yield canvas
    out = os.path.join(OUT_DIR, "intro.mp4")
    encode(frames(), out)
    print(out)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["intro"]:
        cmd_intro()
    elif len(args) == 3 and args[0] in ("stills", "render"):
        (cmd_stills if args[0] == "stills" else cmd_render)(args[1], args[2])
    else:
        print(__doc__)
