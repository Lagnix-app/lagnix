"""Лого PulseFPS для верху бічного меню: мініатюрний робот, що "дихає" й
кліпає, напис Pulse/FPS і тонка лінія пульсу, що біжить під ним.

Анімація йде на ~24 кадри/с (досить для декоративного ефекту, що працює
безперервно весь час роботи програми) через власний after()-цикл із
time.perf_counter(), у стилі ui/widgets/cleaner_bot.py.
"""

from __future__ import annotations

import math
import random
import time
import tkinter as tk

import customtkinter as ctk

from ui import theme

_FRAME_MS = 42  # ~24 fps
_ICON_SIZE = 44
_LINE_HEIGHT = 14

_TEXT_TEAL = "#3ed4ce"  # суміш ACCENT_GREEN/ACCENT_BLUE — "зелено-блакитний"

_TRAIL_LEN = 7


class LogoWidget(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(1, weight=1)

        self._icon = tk.Canvas(
            self, width=_ICON_SIZE, height=_ICON_SIZE, bg=theme.BG_PANEL,
            highlightthickness=0,
        )
        self._icon.grid(row=0, column=0, rowspan=2, padx=(4, 8), pady=(4, 0), sticky="w")

        text_row = ctk.CTkFrame(self, fg_color="transparent")
        text_row.grid(row=0, column=1, sticky="w", pady=(8, 0))
        ctk.CTkLabel(
            text_row, text="Pulse", text_color="#ffffff",
            font=ctk.CTkFont(family="Segoe UI", size=19, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            text_row, text="FPS", text_color=_TEXT_TEAL,
            font=ctk.CTkFont(family="Segoe UI", size=19, weight="bold"),
        ).pack(side="left")

        self._line = tk.Canvas(
            self, width=1, height=_LINE_HEIGHT, bg=theme.BG_PANEL, highlightthickness=0,
        )
        self._line.grid(row=1, column=1, sticky="ew", padx=(0, 4), pady=(0, 6))
        self._line.bind("<Configure>", self._on_line_configure)
        self._line_w = 160

        self._elapsed = 0.0
        self._blink = False
        self._blink_timer = random.uniform(1.8, 3.2)
        self._trail: list[tuple[float, float]] = []
        self._after_id = None
        self._last_tick = None
        self._paused = False
        self._eyes_closed = None
        # кольори шлейфу залежать лише від номера крапки — рахуємо один раз
        self._trail_colors = [
            theme.lerp_color(self, _TEXT_TEAL, theme.BG_PANEL, i / _TRAIL_LEN) for i in range(_TRAIL_LEN)
        ]

        self._build_icon_items()
        self._build_line_items()

        self.bind("<Destroy>", self._on_destroy)
        self._tick()

    # ------------------------------------------------------------- setup

    def _build_icon_items(self) -> None:
        c = self._icon
        self._antenna_line = c.create_line(0, 0, 0, 0, fill=theme.TEXT_DIM, width=2)
        self._antenna_glow = c.create_oval(0, 0, 0, 0, fill=theme.ACCENT_GREEN, outline="")
        self._body = c.create_oval(0, 0, 0, 0, fill=theme.ACCENT_BLUE, outline=theme.ACCENT_BLUE_DIM, width=2)
        self._screen = c.create_rectangle(0, 0, 0, 0, fill=theme.BG_MAIN, outline="")
        self._eye_l = c.create_arc(0, 0, 0, 0, start=110, extent=140, style="arc", outline=theme.ACCENT_GREEN, width=2)
        self._eye_r = c.create_arc(0, 0, 0, 0, start=-70, extent=140, style="arc", outline=theme.ACCENT_GREEN, width=2)

    def _build_line_items(self) -> None:
        c = self._line
        self._track = c.create_line(0, 0, 0, 0, fill=theme.BORDER, width=1, smooth=True)
        self._trail_items = [
            c.create_oval(0, 0, 0, 0, fill=_TEXT_TEAL, outline="", state="hidden") for _ in range(_TRAIL_LEN)
        ]
        self._trail_shown = False

    def _on_line_configure(self, event) -> None:
        self._line_w = max(event.width, 40)

    # ------------------------------------------------------------- loop

    def _on_destroy(self, event) -> None:
        if event.widget is self and self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def set_paused(self, paused: bool) -> None:
        """Пауза, поки вікно згорнуте чи сховане в трей (нікому показувати)."""
        if paused == self._paused:
            return
        self._paused = paused
        if paused:
            if self._after_id is not None:
                self.after_cancel(self._after_id)
                self._after_id = None
            self._last_tick = None
        elif self._after_id is None:
            self._tick()

    def _tick(self) -> None:
        if not self.winfo_exists():
            self._after_id = None
            return

        if not theme.robot_animation_enabled():
            # Лишає останній намальований кадр на місці (без "заморожування"
            # посеред руху при першому вимкненні) й перевіряє прапорець
            # рідше, щоб не гріти слабкий ПК марними after()-циклами.
            self._last_tick = None
            self._after_id = self.after(200, self._tick)
            return

        now = time.perf_counter()
        dt = now - self._last_tick if self._last_tick is not None else _FRAME_MS / 1000.0
        dt = min(dt, 0.2)
        self._last_tick = now
        self._elapsed += dt

        self._blink_timer -= dt
        if self._blink_timer <= 0:
            self._blink = not self._blink
            self._blink_timer = 0.1 if self._blink else random.uniform(2.2, 4.0)

        self._render_icon()
        self._render_line()

        self._after_id = self.after(_FRAME_MS, self._tick)

    # ------------------------------------------------------------ render

    def _render_icon(self) -> None:
        c = self._icon
        t = self._elapsed
        cx, cy = _ICON_SIZE / 2, _ICON_SIZE / 2 + 3
        radius = 14 + math.sin(t * 1.3) * 0.8  # дихання

        c.coords(self._antenna_line, cx, cy - radius, cx, cy - radius - 9)
        glow_r = 3.0 + math.sin(t * 5.0) * 1.2
        top = cy - radius - 9
        c.coords(self._antenna_glow, cx - glow_r, top - glow_r, cx + glow_r, top + glow_r)

        c.coords(self._body, cx - radius, cy - radius, cx + radius, cy + radius)

        panel_w, panel_h = radius * 1.05, radius * 0.75
        c.coords(self._screen, cx - panel_w / 2, cy - panel_h / 2, cx + panel_w / 2, cy + panel_h / 2)

        eye_h = 1.5 if self._blink else panel_h * 0.6
        eye_w = panel_w * 0.24
        for dx, item in ((-panel_w * 0.26, self._eye_l), (panel_w * 0.26, self._eye_r)):
            ex = cx + dx
            c.coords(item, ex - eye_w / 2, cy - eye_h / 2, ex + eye_w / 2, cy + eye_h / 2)
        if self._blink != self._eyes_closed:
            self._eyes_closed = self._blink
            state = "hidden" if self._blink else "normal"
            c.itemconfigure(self._eye_l, state=state)
            c.itemconfigure(self._eye_r, state=state)

    def _render_line(self) -> None:
        c = self._line
        w = self._line_w
        h = _LINE_HEIGHT
        mid = h / 2

        points = []
        n = 24
        for i in range(n + 1):
            x = w * i / n
            y = mid + math.sin(x * 0.045 + self._elapsed * 1.6) * (h * 0.28)
            points.extend((x, y))
        c.coords(self._track, *points)

        speed = w / 2.6  # px/s, одне проходження за ~2.6с
        head_x = (self._elapsed * speed) % (w + 20) - 10
        head_y = mid + math.sin(head_x * 0.045 + self._elapsed * 1.6) * (h * 0.28)

        self._trail.insert(0, (head_x, head_y))
        del self._trail[_TRAIL_LEN:]

        for i, item in enumerate(self._trail_items):
            if i >= len(self._trail):
                continue
            x, y = self._trail[i]
            r = 3.2 * (1 - i / _TRAIL_LEN * 0.8)
            c.coords(item, x - r, y - r, x + r, y + r)
        if len(self._trail) == _TRAIL_LEN and self._trail_shown is False:
            for item, color in zip(self._trail_items, self._trail_colors):
                c.itemconfigure(item, fill=color, state="normal")
            self._trail_shown = True
