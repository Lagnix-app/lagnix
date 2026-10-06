"""Рушій запису промо-відео: покадровий запис клієнтської області вікна Lagnix + «часова шкала».

Сценарій — генератор: кожен `yield` повертає кількість секунд, яку треба почекати до наступного
кроку. Між кроками рушій безперервно оновлює Tk і знімає вікно (mss, ~60 к/с) у JPEG-файли;
до кожного кадру пишеться реальний час. Курсор НЕ рухає справжню мишу й не потрапляє в кадр:
сценарій лише записує його ключові точки (`move`/`click`), а малює курсор монтаж (tools/promo_render.py)
рівно 60 к/с — тому він плавний, незалежно від того, скільки кадрів встигло зняти вікно.

Усе, що показує сценарій, — імітація: рушій не викликає жодних функцій, що чіпають систему.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from core.logging_setup import get_logger

_logger = get_logger(__name__)

TARGET_FPS = 60
SETTLE_S = 2.5          # прогрів перед стартом запису (кадри робота, перша розкладка)
TAIL_S = 0.5            # скільки ще знімати після кінця сценарію
JPEG_QUALITY = 94
MAX_BACKLOG = 160       # кадрів у черзі запису на диск


class Player:
    def __init__(self, root, outdir: str, rect_fn, scenario: str, lang: str, layout: dict):
        self.root = root
        self.outdir = outdir
        self.frames_dir = os.path.join(outdir, "frames")
        self.rect_fn = rect_fn
        self.meta = {"scenario": scenario, "lang": lang, "layout": layout}
        self.sched = 0.0                 # логічний час поточного кроку сценарію
        self.cursor_pos: tuple[float, float] | None = None
        self.segments: list[list] = []   # [t0, t1, x0, y0, x1, y1, bend]
        self.clicks: list[float] = []
        self.cursor_vis: list[list] = []  # [t, bool]
        self.cues: dict[str, float] = {}
        self.captions: list[list] = []   # [t0, t1, ключ] — текст підбирає монтаж за мовою
        self.frames: list[float] = []
        self._tickers: list = []
        self._t0 = 0.0
        self._hover = None

    # -------------------------------------------------------------- координати

    def origin(self) -> tuple[int, int]:
        left, top, _r, _b = self.rect_fn(self.root)
        return left, top

    def center(self, widget, dx: float = 0.0, dy: float = 0.0) -> tuple[float, float]:
        """Центр віджета в координатах знімка (клієнтська область вікна)."""
        ox, oy = self.origin()
        widget.update_idletasks()
        return (widget.winfo_rootx() - ox + widget.winfo_width() / 2 + dx,
                widget.winfo_rooty() - oy + widget.winfo_height() / 2 + dy)

    # ------------------------------------------------------------------ курсор

    def show_cursor(self, visible: bool = True) -> None:
        self.cursor_vis.append([self.sched, bool(visible)])

    def place_cursor(self, target) -> None:
        """Миттєво ставить курсор (без руху) — для початкової точки за межами кадру."""
        self.cursor_pos = self._xy(target)

    def _xy(self, target) -> tuple[float, float]:
        return tuple(target) if isinstance(target, (tuple, list)) else self.center(target)

    def move(self, target, dur: float, bend: float = 0.0) -> float:
        """Плавний рух курсора до точки/віджета за dur с (ease in-out, bend — вигин траєкторії, px)."""
        x1, y1 = self._xy(target)
        x0, y0 = self.cursor_pos or (x1, y1)
        self.segments.append([self.sched, self.sched + dur, x0, y0, x1, y1, bend])
        self.cursor_pos = (x1, y1)
        return dur

    def shake(self, dur: float, amp: float = 3.0, hz: float = 14.0) -> float:
        """Тремтіння курсора на місці: кілька крихітних рухів."""
        x, y = self.cursor_pos
        n = max(2, int(dur * hz))
        step = dur / n
        cx, cy = x, y
        for i in range(n):
            sign = 1 if i % 2 == 0 else -1
            nx, ny = x + sign * amp * (0.6 + 0.4 * ((i * 7) % 3) / 2), y + (amp * 0.5 if i % 3 == 0 else -amp * 0.4)
            self.segments.append([self.sched + i * step, self.sched + (i + 1) * step, cx, cy, nx, ny, 0.0])
            cx, cy = nx, ny
        self.segments.append([self.sched + dur, self.sched + dur + 0.04, cx, cy, x, y, 0.0])
        return dur

    def click(self) -> None:
        self.clicks.append(self.sched)

    def caption(self, key: str, dur: float) -> None:
        self.captions.append([self.sched, self.sched + dur, key])

    def cue(self, name: str) -> None:
        self.cues[name] = self.sched

    # ----------------------------------------------------------------- віджети

    def hover(self, widget, on: bool = True) -> None:
        """Підсвічує кнопку так, ніби над нею курсор (події <Enter>/<Leave>, справжня миша не рухається)."""
        for part in (getattr(widget, "_canvas", None), getattr(widget, "_text_label", None), widget):
            if part is None:
                continue
            try:
                part.event_generate("<Enter>" if on else "<Leave>", x=5, y=5)
            except Exception:
                pass

    def on_tick(self, fn) -> None:
        """fn(t) викликається на кожному кадрі запису (анімації самих віджетів)."""
        self._tickers.append(fn)

    # ------------------------------------------------------------------ запис

    def _now(self) -> float:
        return time.perf_counter() - self._t0

    def run(self, scenario) -> None:
        import mss
        from PIL import Image

        os.makedirs(self.frames_dir, exist_ok=True)
        for name in os.listdir(self.frames_dir):
            os.remove(os.path.join(self.frames_dir, name))
        root = self.root
        end = time.perf_counter() + SETTLE_S
        while time.perf_counter() < end:  # прогрів
            root.update()
            time.sleep(0.01)

        left, top, right, bottom = self.rect_fn(root)
        region = {"left": left, "top": top, "width": right - left, "height": bottom - top}
        self.meta["size"] = [region["width"], region["height"]]

        pool = ThreadPoolExecutor(max_workers=4)
        slots = threading.Semaphore(MAX_BACKLOG)

        def save(index: int, size, raw: bytes) -> None:
            try:
                img = Image.frombytes("RGB", size, raw, "raw", "BGRX")
                img.save(os.path.join(self.frames_dir, f"{index:05d}.jpg"), quality=JPEG_QUALITY, subsampling=0)
            finally:
                slots.release()

        gen = scenario(self)
        resume = 0.0
        finished_at = None
        frame_dt = 1.0 / TARGET_FPS
        next_frame = 0.0
        self._t0 = time.perf_counter()
        with mss.mss() as sct:
            while True:
                now = self._now()
                for fn in self._tickers:
                    fn(now)
                while finished_at is None and now >= resume:
                    self.sched = resume
                    try:
                        resume += float(next(gen) or 0.0)
                    except StopIteration:
                        finished_at = now + TAIL_S
                        self.meta["duration"] = resume + TAIL_S
                        break
                root.update()
                t = self._now()
                shot = sct.grab(region)
                slots.acquire()
                pool.submit(save, len(self.frames), shot.size, shot.bgra)
                self.frames.append(round(t, 4))
                if finished_at is not None and now >= finished_at:
                    break
                next_frame += frame_dt
                wait = next_frame - self._now()
                if wait > 0.002:
                    time.sleep(wait - 0.001)
                elif wait < -0.25:
                    next_frame = self._now()  # сильно відстали — не нагоняємо
        pool.shutdown(wait=True)
        self._write_timeline()

    def _write_timeline(self) -> None:
        data = dict(self.meta, frames=self.frames, cursor=self.segments, clicks=self.clicks,
                    cursor_vis=self.cursor_vis, cues=self.cues, captions=self.captions)
        with open(os.path.join(self.outdir, "timeline.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        fps = len(self.frames) / max(self.frames[-1], 1e-6)
        print(f"promo: {len(self.frames)} кадрів, {self.frames[-1]:.1f} с, ~{fps:.0f} к/с -> {self.outdir}")
