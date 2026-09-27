"""Мультяшна анімація робота-прибиральника для довгих операцій (очищення,
видалення програм тощо) — картка з Canvas-анімацією, текстом і прогрес-баром,
яку можна перевикористовувати на різних вкладках.

Використання: створити віджет і один раз розмістити його в макеті (pack/grid),
далі керувати лише через start()/update()/finish()/hide() — сам віджет
запам'ятовує спосіб розміщення і показує/ховає себе автоматично.
"""

import math
import random
import tkinter as tk

import customtkinter as ctk

FRAME_MS = 33  # ~30 кадрів/с

_CANVAS_BG = "#1a1a1a"
_BODY_MAIN = "#3b8ed0"
_BODY_DARK = "#2d6ea3"
_SCREEN_BG = "#12181f"
_EYE_COLOR = "#dce4ee"
_ACCENT_GREEN = "#2fa572"
_ACCENT_PURPLE = "#c77dff"
_ACCENT_ORANGE = "#e0a52f"
_BROOM_HANDLE = "#8a5a2b"

_FILE_COLORS = (_BODY_MAIN, _ACCENT_GREEN, _ACCENT_PURPLE)
_SPARK_COLORS = (_ACCENT_ORANGE, _EYE_COLOR, _ACCENT_PURPLE)
_CONFETTI_COLORS = (_BODY_MAIN, _ACCENT_GREEN, _ACCENT_PURPLE, _ACCENT_ORANGE, "#e05252")


class CleanerBotAnimation(ctk.CTkFrame):
    """Картка з роботом-прибиральником, текстом статусу і прогрес-баром."""

    def __init__(self, master, height: int = 150):
        super().__init__(master, corner_radius=10)

        self._canvas_w = 320
        self._canvas_h = height
        self._elapsed = 0.0
        self._state = "hidden"  # hidden | running | finishing | shrug
        self._after_id = None
        self._hide_after_id = None
        self._particles: list[dict] = []
        self._spawn_timers = {"dust": 0.0, "file": 0.0, "spark": 0.0}
        self._blink = False
        self._blink_timer = random.uniform(1.5, 3.0)
        self._geo_manager = None
        self._geo_options = None

        self.canvas = tk.Canvas(self, height=height, bg=_CANVAS_BG, highlightthickness=0)
        self.canvas.pack(fill="x", padx=12, pady=(12, 8))
        self.canvas.bind("<Configure>", self._on_configure)

        self.label = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=13, weight="bold"))
        self.label.pack(padx=12, pady=(0, 6))

        self.progress = ctk.CTkProgressBar(self, height=10)
        self.progress.set(0.0)
        self.progress.pack(fill="x", padx=12, pady=(0, 14))

        self.bind("<Destroy>", self._on_destroy)

    # ------------------------------------------------------------ public

    def start(self, text: str) -> None:
        """Показує анімацію й переходить у стан «прибирання»."""
        self._capture_geometry()
        self._reappear()
        self._state = "running"
        self._elapsed = 0.0
        self._particles.clear()
        self._spawn_timers = {"dust": 0.0, "file": 0.0, "spark": 0.0}
        self._cancel_hide()
        self.label.configure(text=text)
        self.progress.set(0.0)
        self._ensure_loop()

    def update(self, text: str, progress: float | None = None) -> None:
        """Оновлює текст і, за наявності, прогрес (0..1) під час роботи."""
        if self._state == "hidden":
            self.start(text)
            return
        self.label.configure(text=text)
        if progress is not None:
            self.progress.set(max(0.0, min(1.0, progress)))

    def finish(self, text: str, success: bool = True) -> None:
        """Завершує анімацію: успіх — стрибок і конфеті, невдача — знизування
        плечима. У обох випадках картка ховається сама за кілька секунд.
        """
        self._capture_geometry()
        self._reappear()
        self._cancel_hide()
        self.label.configure(text=text)

        if success:
            self._state = "finishing"
            self._elapsed = 0.0
            self.progress.set(1.0)
            self._spawn_confetti()
            delay_ms = 3200
        else:
            self._state = "shrug"
            self._elapsed = 0.0
            delay_ms = 2600

        self._ensure_loop()
        self._hide_after_id = self.after(delay_ms, self.hide)

    def hide(self) -> None:
        """Ховає картку й зупиняє анімацію, зберігаючи місце в макеті."""
        self._cancel_hide()
        self._stop_loop()
        self._state = "hidden"
        self._particles.clear()

        self._capture_geometry()
        manager = self.winfo_manager()
        if manager == "pack":
            self.pack_forget()
        elif manager == "grid":
            self.grid_forget()
        elif manager == "place":
            self.place_forget()

    # -------------------------------------------------------- geometry

    def _capture_geometry(self) -> None:
        if self._geo_manager is not None:
            return
        manager = self.winfo_manager()
        if manager == "pack":
            self._geo_manager = "pack"
            self._geo_options = self.pack_info()
        elif manager == "grid":
            self._geo_manager = "grid"
            self._geo_options = self.grid_info()
        elif manager == "place":
            self._geo_manager = "place"
            self._geo_options = self.place_info()

    def _reappear(self) -> None:
        if self.winfo_manager():
            return
        if self._geo_manager == "pack":
            self.pack(**self._geo_options)
        elif self._geo_manager == "grid":
            self.grid(**self._geo_options)
        elif self._geo_manager == "place":
            self.place(**self._geo_options)

    def _on_configure(self, event) -> None:
        self._canvas_w = event.width
        self._canvas_h = event.height

    # ------------------------------------------------------------- loop

    def _ensure_loop(self) -> None:
        if self._after_id is None:
            self._tick()

    def _stop_loop(self) -> None:
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None

    def _cancel_hide(self) -> None:
        if self._hide_after_id is not None:
            self.after_cancel(self._hide_after_id)
            self._hide_after_id = None

    def _tick(self) -> None:
        if not self.winfo_exists():
            self._after_id = None
            return

        dt = FRAME_MS / 1000.0
        self._elapsed += dt
        self._update_particles(dt)
        self._maybe_spawn(dt)
        self._render_scene()
        self._after_id = self.after(FRAME_MS, self._tick)

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            self._stop_loop()
            self._cancel_hide()

    # -------------------------------------------------------- particles

    def _robot_center(self, w: float, h: float) -> tuple[float, float]:
        return w / 2, h - 14 - 44

    def _random_spot(self, w: float, h: float, exclude_r: float) -> tuple[float, float]:
        cx, cy = self._robot_center(w, h)
        x, y = w / 2, h / 2
        for _ in range(10):  # кілька спроб знайти точку подалі від робота
            x = random.uniform(18, max(19, w - 18))
            y = random.uniform(14, max(15, h - 26))
            if math.hypot(x - cx, y - cy) > exclude_r:
                break
        return x, y

    def _maybe_spawn(self, dt: float) -> None:
        if self._state != "running":
            return
        w = max(self._canvas_w, 60)
        h = max(self._canvas_h, 60)

        self._spawn_timers["dust"] -= dt
        if self._spawn_timers["dust"] <= 0 and self._count("dust") < 5:
            x, y = self._random_spot(w, h, 48)
            self._particles.append({
                "kind": "dust", "x": x, "y": y, "life": 0.0,
                "max_life": random.uniform(0.8, 1.3), "size": random.uniform(3, 6),
                "color": "#5a6472",
            })
            self._spawn_timers["dust"] = random.uniform(0.4, 0.9)

        self._spawn_timers["file"] -= dt
        if self._spawn_timers["file"] <= 0 and self._count("file") < 3:
            x, y = self._random_spot(w, h, 54)
            self._particles.append({
                "kind": "file", "x": x, "y": y, "life": 0.0,
                "max_life": random.uniform(1.2, 1.8), "size": random.uniform(9, 13),
                "color": random.choice(_FILE_COLORS),
            })
            self._spawn_timers["file"] = random.uniform(1.0, 1.9)

        self._spawn_timers["spark"] -= dt
        if self._spawn_timers["spark"] <= 0 and self._count("spark") < 6:
            x, y = self._random_spot(w, h, 20)
            self._particles.append({
                "kind": "spark", "x": x, "y": y, "life": 0.0,
                "max_life": random.uniform(0.4, 0.7), "size": random.uniform(3, 6),
                "color": random.choice(_SPARK_COLORS),
            })
            self._spawn_timers["spark"] = random.uniform(0.25, 0.55)

        self._blink_timer -= dt
        if self._blink_timer <= 0:
            self._blink = not self._blink
            self._blink_timer = 0.12 if self._blink else random.uniform(2.0, 4.0)

    def _count(self, kind: str) -> int:
        return sum(1 for p in self._particles if p["kind"] == kind)

    def _spawn_confetti(self) -> None:
        w = max(self._canvas_w, 60)
        h = max(self._canvas_h, 60)
        cx, cy = self._robot_center(w, h)
        for _ in range(26):
            self._particles.append({
                "kind": "confetti",
                "x": cx + random.uniform(-12, 12),
                "y": cy - 24,
                "vx": random.uniform(-90, 90),
                "vy": random.uniform(-170, -70),
                "rot": random.uniform(0, math.pi * 2),
                "vrot": random.uniform(-6, 6),
                "size": random.uniform(4, 7),
                "color": random.choice(_CONFETTI_COLORS),
            })

    def _update_particles(self, dt: float) -> None:
        alive = []
        limit_y = self._canvas_h + 30
        for p in self._particles:
            if p["kind"] == "confetti":
                p["vy"] += 220 * dt
                p["x"] += p["vx"] * dt
                p["y"] += p["vy"] * dt
                p["rot"] += p["vrot"] * dt
                if p["y"] <= limit_y:
                    alive.append(p)
                continue

            p["life"] += dt
            if p["life"] < p["max_life"]:
                alive.append(p)
        self._particles = alive

    # ---------------------------------------------------------- drawing

    def _render_scene(self) -> None:
        c = self.canvas
        c.delete("all")
        w = max(self._canvas_w, 60)
        h = max(self._canvas_h, 60)

        self._draw_particles(c, ("dust", "file"))
        self._draw_robot(c, w, h)
        self._draw_particles(c, ("spark", "confetti"))

    def _draw_particles(self, c: tk.Canvas, kinds: tuple[str, ...]) -> None:
        for p in self._particles:
            if p["kind"] not in kinds:
                continue

            if p["kind"] == "dust":
                frac = p["life"] / p["max_life"]
                r = p["size"] * (1 - frac)
                if r <= 0.4:
                    continue
                y = p["y"] - frac * 14
                c.create_oval(p["x"] - r, y - r, p["x"] + r, y + r, fill=p["color"], outline="")

            elif p["kind"] == "file":
                frac = p["life"] / p["max_life"]
                s = p["size"] * (1 - frac * 0.9)
                if s <= 1:
                    continue
                x, y = p["x"], p["y"] - frac * 20
                fold = s * 0.35
                c.create_polygon(
                    x - s, y - s, x + s - fold, y - s, x + s, y - s + fold,
                    x + s, y + s, x - s, y + s,
                    fill=p["color"], outline=_SCREEN_BG,
                )

            elif p["kind"] == "spark":
                frac = p["life"] / p["max_life"]
                s = p["size"] * math.sin(min(frac, 1.0) * math.pi)
                if s <= 0.3:
                    continue
                x, y = p["x"], p["y"]
                c.create_line(x - s, y, x + s, y, fill=p["color"], width=2)
                c.create_line(x, y - s, x, y + s, fill=p["color"], width=2)

            elif p["kind"] == "confetti":
                s = p["size"]
                rot = p["rot"]
                dx, dy = math.cos(rot) * s, math.sin(rot) * s
                dx2 = math.cos(rot + math.pi / 2) * s * 0.5
                dy2 = math.sin(rot + math.pi / 2) * s * 0.5
                x, y = p["x"], p["y"]
                c.create_polygon(
                    x - dx - dx2, y - dy - dy2, x + dx - dx2, y + dy - dy2,
                    x + dx + dx2, y + dy + dy2, x - dx + dx2, y - dy + dy2,
                    fill=p["color"], outline="",
                )

    def _draw_robot(self, c: tk.Canvas, w: float, h: float) -> None:
        ground_y = h - 14
        cx = w / 2
        state = self._state
        t = self._elapsed

        if state == "finishing":
            bt = min(t, 1.6)
            bounce = abs(math.sin(bt * 6.0)) * 20 * math.exp(-bt * 1.3)
        elif state == "shrug":
            bounce = math.sin(t * 2.0) * 1.5
        elif state == "running":
            bounce = math.sin(t * 3.4) * 3.5
        else:
            bounce = 0.0

        radius = 30
        cy = ground_y - radius - 14 - bounce

        shadow_scale = max(0.4, 1 - bounce / 24)
        sw = 44 * shadow_scale
        c.create_oval(cx - sw, ground_y + 2, cx + sw, ground_y + 9, fill="black", outline="", stipple="gray50")

        # колеса
        for dx in (-15, 15):
            c.create_oval(cx + dx - 7, cy + radius - 6, cx + dx + 7, cy + radius + 7,
                          fill=_SCREEN_BG, outline=_BODY_DARK)

        # тіло
        c.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                      fill=_BODY_MAIN, outline=_BODY_DARK, width=2)

        # антена з лампочкою
        c.create_line(cx, cy - radius, cx, cy - radius - 12, fill=_BODY_DARK, width=2)
        light_r = 4 + math.sin(t * 6.0) * 1.3
        light_color = _ACCENT_GREEN if state != "shrug" else _ACCENT_ORANGE
        c.create_oval(cx - light_r, cy - radius - 12 - light_r, cx + light_r, cy - radius - 12 + light_r,
                      fill=light_color, outline="")

        self._draw_arms_and_broom(c, cx, cy, radius, state, t)

        # екран-обличчя
        panel_w, panel_h = 30, 20
        py = cy - 4
        c.create_rectangle(cx - panel_w / 2, py - panel_h / 2, cx + panel_w / 2, py + panel_h / 2,
                           fill=_SCREEN_BG, outline=_BODY_DARK)

        eye_h = 1 if self._blink and state != "shrug" else 4
        for dx in (-7, 7):
            ex, ey = cx + dx, py - 3
            c.create_oval(ex - 4, ey - eye_h, ex + 4, ey + eye_h, fill=_EYE_COLOR, outline="")

        if state == "shrug":
            c.create_line(cx - 6, py + 6, cx + 6, py + 6, fill=_EYE_COLOR, width=2)
        elif state == "finishing":
            c.create_arc(cx - 8, py - 1, cx + 8, py + 11, start=200, extent=140,
                        style="arc", outline=_EYE_COLOR, width=2)
        else:
            c.create_arc(cx - 6, py + 1, cx + 6, py + 8, start=200, extent=140,
                        style="arc", outline=_EYE_COLOR, width=2)

    def _draw_arms_and_broom(self, c: tk.Canvas, cx: float, cy: float, radius: float,
                              state: str, t: float) -> None:
        if state == "shrug":
            for side in (-1, 1):
                sx, sy = cx + side * (radius - 6), cy + 6
                ex, ey = sx + side * 14, sy - 18
                c.create_line(sx, sy, ex, ey, fill=_BODY_DARK, width=4, capstyle="round")
            return

        # ліва рука — нерухома опора
        lx, ly = cx - (radius - 6), cy + 6
        c.create_line(lx, ly, lx - 9, ly + 10, fill=_BODY_DARK, width=4, capstyle="round")

        # права рука тримає мітлу, що замітає
        rx, ry = cx + (radius - 6), cy + 6
        if state == "running":
            angle = math.radians(45 + math.sin(t * 5.0) * 28)
        else:
            angle = math.radians(50)

        hand_x = rx + math.cos(angle) * 18
        hand_y = ry - math.sin(angle) * 18
        c.create_line(rx, ry, hand_x, hand_y, fill=_BODY_DARK, width=4, capstyle="round")

        tip_x = rx + math.cos(angle) * 40
        tip_y = ry - math.sin(angle) * 40
        c.create_line(hand_x, hand_y, tip_x, tip_y, fill=_BROOM_HANDLE, width=3, capstyle="round")

        perp = angle + math.pi / 2
        spread = 9
        base_x = hand_x + (tip_x - hand_x) * 0.7
        base_y = hand_y + (tip_y - hand_y) * 0.7
        p1 = (tip_x + math.cos(perp) * spread, tip_y - math.sin(perp) * spread)
        p2 = (tip_x - math.cos(perp) * spread, tip_y + math.sin(perp) * spread)
        c.create_polygon(base_x, base_y, p1[0], p1[1], tip_x, tip_y, p2[0], p2[1],
                         fill=_ACCENT_ORANGE, outline="")

        if state == "running":
            deg = math.degrees(angle)
            c.create_arc(tip_x - 13, tip_y - 13, tip_x + 13, tip_y + 13,
                        start=deg - 35, extent=25, style="arc", outline="#4a5568")
