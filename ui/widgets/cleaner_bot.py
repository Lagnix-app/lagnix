"""Мультяшна анімація робота-прибиральника для довгих операцій (очищення,
видалення програм тощо) — картка з Canvas-анімацією, текстом і прогрес-баром,
яку можна перевикористовувати на різних вкладках.

Використання: створити віджет і один раз розмістити його в макеті (pack/grid),
далі керувати лише через start()/update()/finish()/hide() — сам віджет
запам'ятовує спосіб розміщення і показує/ховає себе автоматично.

Продуктивність: цикл анімації працює на ~60 кадрів/с (after(16 мс) з
корекцією під реальний час кадру), рух рахується від фактично сплиненого
часу (time.perf_counter()), а не від номера кадру, тож затримки в системі
не викликають ривків. Canvas-фігури створюються один раз при побудові
віджета й пулу часток, а кожен кадр лише оновлює їхні coords()/itemconfig() —
canvas.delete("all") не використовується.
"""

import ctypes
import math
import platform
import random
import time
import tkinter as tk
from collections import deque

import customtkinter as ctk

from core import sounds
from ui import theme

_IS_WINDOWS = platform.system() == "Windows"

FRAME_INTERVAL_MS = 16  # ~60 кадрів/с
MAX_FRAME_DT = 0.1  # захист від стрибка після затримки (згорнуте вікно тощо)
STATUS_UPDATE_INTERVAL_S = 0.1  # текст і прогрес-бар оновлюються не частіше 10 р/с

DEBUG_FPS = False  # тимчасовий лічильник FPS у кутку для перевірки продуктивності

_CANVAS_BG = "#0d1321"
_BODY_MAIN = "#4fc3ff"
_BODY_DARK = "#1f7ae0"
_SCREEN_BG = "#0d1321"
_EYE_COLOR = "#2ee59d"
_ACCENT_GREEN = "#2ee59d"
_ACCENT_PURPLE = "#c77dff"
_ACCENT_ORANGE = "#e0a52f"
_BROOM_HANDLE = "#8a5a2b"
_ANTENNA_STICK = "#8a94a6"

_FILE_COLORS = (_BODY_MAIN, _ACCENT_GREEN, _ACCENT_PURPLE)
_SPARK_COLORS = (_ACCENT_ORANGE, _EYE_COLOR, _ACCENT_PURPLE)
_CONFETTI_COLORS = (_BODY_MAIN, _ACCENT_GREEN, _ACCENT_PURPLE, _ACCENT_ORANGE, "#e05252")

DUST_MAX = 3
FILE_MAX = 2
SPARK_MAX = 4
CONFETTI_MAX = 16


def _ease_out_quad(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) * (1 - x)


class _FpsCounter:
    """Тимчасовий лічильник кадрів/с у кутку канви (увімкнено через DEBUG_FPS)."""

    def __init__(self, canvas: tk.Canvas):
        self._times: deque = deque(maxlen=180)
        self._item = canvas.create_text(
            6, 6, anchor="nw", fill="#39ff14", font=("Consolas", 10, "bold"), text="FPS: --",
        )
        self._canvas = canvas

    def tick(self, now: float) -> None:
        self._times.append(now)
        while len(self._times) > 1 and now - self._times[0] > 0.5:
            self._times.popleft()
        if len(self._times) >= 2:
            fps = (len(self._times) - 1) / (self._times[-1] - self._times[0])
            self._canvas.itemconfigure(self._item, text=f"FPS: {fps:4.1f}")


class CleanerBotAnimation(ctk.CTkFrame):
    """Картка з роботом-прибиральником, текстом статусу і прогрес-баром."""

    def __init__(self, master, height: int = 150):
        super().__init__(master, corner_radius=10)

        self._canvas_w = 320
        self._canvas_h = height
        self._elapsed = 0.0
        self._state = "hidden"  # hidden | running | finishing | shrug
        self._tool = "broom"  # broom | scan
        self._after_id = None
        self._hide_after_id = None
        self._last_tick_perf = None
        self._last_ui_update = 0.0
        self._pending_label_text = None
        self._pending_progress = None
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

        self._build_static_items()
        self._render_scene()
        self._fps_counter = _FpsCounter(self.canvas) if DEBUG_FPS else None

        self.bind("<Destroy>", self._on_destroy)

    # ------------------------------------------------------------ public

    def start(self, text: str, tool: str = "broom") -> None:
        """Показує анімацію й переходить у стан «прибирання».

        tool="scan" перемикає робота в «режим вимірювання»: замітку
        заміняє антена з пульсуючими хвилями сигналу (для мережевих тестів).
        """
        self._capture_geometry()
        self._reappear()
        self._state = "running"
        self._tool = tool
        self._elapsed = 0.0
        self._last_tick_perf = None
        self._pending_label_text = None
        self._pending_progress = None
        self._deactivate_all_particles()
        self._spawn_timers = {"dust": 0.0, "file": 0.0, "spark": 0.0}
        self._cancel_hide()
        self.label.configure(text=text)
        self.progress.set(0.0)
        self._render_scene()
        self._ensure_loop()

    def update(self, text: str, progress: float | None = None) -> None:
        """Оновлює текст і, за наявності, прогрес (0..1) під час роботи.

        Викликається скільки завгодно часто зовнішнім кодом — фактичне
        оновлення віджетів відбувається не частіше STATUS_UPDATE_INTERVAL_S
        (з циклу анімації), тож часті виклики не навантажують інтерфейс.
        """
        if self._state == "hidden":
            self.start(text)
            return
        self._pending_label_text = text
        self._pending_progress = progress

    def finish(self, text: str, success: bool = True) -> None:
        """Завершує анімацію: успіх — стрибок і конфеті, невдача — знизування
        плечима. У обох випадках картка ховається сама за кілька секунд.
        """
        self._capture_geometry()
        self._reappear()
        self._cancel_hide()
        self._pending_label_text = None
        self._pending_progress = None
        self.label.configure(text=text)

        if success:
            self._state = "finishing"
            self._elapsed = 0.0
            self.progress.set(1.0)
            self._spawn_confetti()
            sounds.play_success()
            delay_ms = 3200
        else:
            self._state = "shrug"
            self._elapsed = 0.0
            sounds.play_error()
            delay_ms = 2600

        self._render_scene()
        self._ensure_loop()
        self._hide_after_id = self.after(delay_ms, self.hide)

    def hide(self) -> None:
        """Ховає картку й зупиняє анімацію, зберігаючи місце в макеті."""
        self._cancel_hide()
        self._stop_loop()
        self._state = "hidden"
        self._deactivate_all_particles()

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
            self._set_high_res_timer(True)
            self._last_tick_perf = time.perf_counter()
            self._tick()

    def _stop_loop(self) -> None:
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
            self._set_high_res_timer(False)

    @staticmethod
    def _set_high_res_timer(enable: bool) -> None:
        """Windows квантує after() до системного тику таймера (~15.6 мс),
        через що after(16) насправді спрацьовує раз ~21 мс (~46 fps замість
        60). timeBeginPeriod(1) підвищує роздільність таймера на час
        анімації; timeEndPeriod(1) повертає її назад, коли цикл зупинено."""
        if not _IS_WINDOWS:
            return
        try:
            if enable:
                ctypes.windll.winmm.timeBeginPeriod(1)
            else:
                ctypes.windll.winmm.timeEndPeriod(1)
        except (AttributeError, OSError):
            pass

    def _cancel_hide(self) -> None:
        if self._hide_after_id is not None:
            self.after_cancel(self._hide_after_id)
            self._hide_after_id = None

    def _tick(self) -> None:
        if not self.winfo_exists():
            self._after_id = None
            return

        now = time.perf_counter()
        dt = now - self._last_tick_perf if self._last_tick_perf is not None else FRAME_INTERVAL_MS / 1000.0
        dt = min(dt, MAX_FRAME_DT)
        self._last_tick_perf = now

        # "Анімація робота" вимкнена (слабкі ПК): робот лишається в
        # статичній позі (намальованій у start()/finish()), рухи й частки
        # не рахуються — але текст/прогрес продовжують оновлюватись нижче.
        if theme.robot_animation_enabled():
            self._elapsed += dt
            self._update_particles(dt)
            self._maybe_spawn(dt)
            self._render_scene()

        self._flush_ui_update(now)

        if self._fps_counter is not None:
            self._fps_counter.tick(now)

        if theme.robot_animation_enabled():
            work_ms = (time.perf_counter() - now) * 1000.0
            delay = max(1, round(FRAME_INTERVAL_MS - work_ms))
        else:
            delay = 100
        self._after_id = self.after(delay, self._tick)

    def _flush_ui_update(self, now: float) -> None:
        """Переносить накопичені update() у label/прогрес-бар, не частіше
        STATUS_UPDATE_INTERVAL_S — незалежно від того, як часто викликали update()."""
        if self._pending_label_text is None and self._pending_progress is None:
            return
        if (now - self._last_ui_update) < STATUS_UPDATE_INTERVAL_S:
            return
        self._last_ui_update = now
        if self._pending_label_text is not None:
            self.label.configure(text=self._pending_label_text)
            self._pending_label_text = None
        if self._pending_progress is not None:
            self.progress.set(max(0.0, min(1.0, self._pending_progress)))
            self._pending_progress = None

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            self._stop_loop()
            self._cancel_hide()

    # -------------------------------------------------------- particles

    def _build_particle_pool(self, count: int, kind_defaults: dict, shape: str = "polygon") -> list:
        pool = []
        for _ in range(count):
            if shape == "oval":
                item = self.canvas.create_oval(0, 0, 0, 0, fill="", outline="", state="hidden")
            else:
                item = self.canvas.create_polygon(0, 0, 0, 0, 0, 0, 0, 0, fill="", outline="", state="hidden")
            slot = dict(kind_defaults)
            slot["item"] = item
            slot["active"] = False
            pool.append(slot)
        return pool

    def _build_static_items(self) -> None:
        c = self.canvas

        # спершу — пул фонових часток (пил/файли), щоб лишались позаду робота
        self._dust_pool = self._build_particle_pool(
            DUST_MAX, {"life": 0.0, "max_life": 1.0, "size": 4.0, "color": "#5a6472"}, shape="oval",
        )
        self._file_pool = self._build_particle_pool(FILE_MAX, {"life": 0.0, "max_life": 1.0, "size": 10.0, "color": _FILE_COLORS[0]})

        # робот — статичні фігури, надалі лише coords()/itemconfig()
        self._items = {}
        self._items["shadow"] = c.create_oval(0, 0, 0, 0, fill="black", outline="", stipple="gray50")
        self._items["wheel_l"] = c.create_oval(0, 0, 0, 0, fill=_SCREEN_BG, outline=_BODY_DARK)
        self._items["wheel_r"] = c.create_oval(0, 0, 0, 0, fill=_SCREEN_BG, outline=_BODY_DARK)
        self._items["body"] = c.create_oval(0, 0, 0, 0, fill=_BODY_MAIN, outline=_BODY_DARK, width=2)
        self._items["antenna_line"] = c.create_line(0, 0, 0, 0, fill=_BODY_DARK, width=2)
        self._items["antenna_light"] = c.create_oval(0, 0, 0, 0, fill=_ACCENT_GREEN, outline="")

        self._items["left_arm"] = c.create_line(0, 0, 0, 0, fill=_BODY_DARK, width=4, capstyle="round")
        self._items["right_arm"] = c.create_line(0, 0, 0, 0, fill=_BODY_DARK, width=4, capstyle="round")

        self._items["broom_handle"] = c.create_line(
            0, 0, 0, 0, fill=_BROOM_HANDLE, width=3, capstyle="round", state="hidden",
        )
        self._items["broom_head"] = c.create_polygon(
            0, 0, 0, 0, 0, 0, 0, 0, fill=_ACCENT_ORANGE, outline="", state="hidden",
        )
        self._items["broom_arc"] = c.create_arc(
            0, 0, 0, 0, start=0, extent=25, style="arc", outline="#4a5568", state="hidden",
        )

        self._items["antenna_stick"] = c.create_line(
            0, 0, 0, 0, fill=_ANTENNA_STICK, width=3, capstyle="round", state="hidden",
        )
        self._items["antenna_tip"] = c.create_oval(0, 0, 0, 0, fill=_ACCENT_GREEN, outline="", state="hidden")
        self._items["signal_arcs"] = [
            c.create_arc(0, 0, 0, 0, start=25, extent=130, style="arc", outline=_ACCENT_GREEN, state="hidden")
            for _ in range(3)
        ]

        self._items["screen"] = c.create_rectangle(0, 0, 0, 0, fill=_SCREEN_BG, outline=_BODY_DARK)
        self._items["eye_l"] = c.create_oval(0, 0, 0, 0, fill=_EYE_COLOR, outline="")
        self._items["eye_r"] = c.create_oval(0, 0, 0, 0, fill=_EYE_COLOR, outline="")
        self._items["mouth_arc"] = c.create_arc(
            0, 0, 0, 0, start=200, extent=140, style="arc", outline=_EYE_COLOR, width=2,
        )
        self._items["mouth_line"] = c.create_line(0, 0, 0, 0, fill=_EYE_COLOR, width=2, state="hidden")

        # пул часток спереду (спалахи/конфеті), щоб лишались над роботом
        self._spark_pool = self._build_particle_pool(SPARK_MAX, {"life": 0.0, "max_life": 1.0, "size": 4.0, "color": _SPARK_COLORS[0]})
        self._confetti_pool = self._build_particle_pool(
            CONFETTI_MAX, {"x": 0.0, "y": 0.0, "vx": 0.0, "vy": 0.0, "rot": 0.0, "vrot": 0.0, "size": 5.0, "color": _CONFETTI_COLORS[0]},
        )

    def _deactivate_all_particles(self) -> None:
        for slot in self._dust_pool + self._file_pool + self._spark_pool + self._confetti_pool:
            slot["active"] = False
            self.canvas.itemconfigure(slot["item"], state="hidden")

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

    def _acquire(self, pool: list) -> dict | None:
        for slot in pool:
            if not slot["active"]:
                return slot
        return None

    def _maybe_spawn(self, dt: float) -> None:
        if self._state != "running":
            return
        w = max(self._canvas_w, 60)
        h = max(self._canvas_h, 60)

        if self._tool == "broom":
            self._spawn_timers["dust"] -= dt
            if self._spawn_timers["dust"] <= 0:
                slot = self._acquire(self._dust_pool)
                if slot is not None:
                    x, y = self._random_spot(w, h, 48)
                    slot.update(
                        active=True, x=x, y=y, life=0.0,
                        max_life=random.uniform(0.8, 1.3), size=random.uniform(3, 6),
                        color="#5a6472",
                    )
                self._spawn_timers["dust"] = random.uniform(0.4, 0.9)

            self._spawn_timers["file"] -= dt
            if self._spawn_timers["file"] <= 0:
                slot = self._acquire(self._file_pool)
                if slot is not None:
                    x, y = self._random_spot(w, h, 54)
                    slot.update(
                        active=True, x=x, y=y, life=0.0,
                        max_life=random.uniform(1.2, 1.8), size=random.uniform(9, 13),
                        color=random.choice(_FILE_COLORS),
                    )
                self._spawn_timers["file"] = random.uniform(1.0, 1.9)

        self._spawn_timers["spark"] -= dt
        if self._spawn_timers["spark"] <= 0:
            slot = self._acquire(self._spark_pool)
            if slot is not None:
                x, y = self._random_spot(w, h, 20)
                slot.update(
                    active=True, x=x, y=y, life=0.0,
                    max_life=random.uniform(0.4, 0.7), size=random.uniform(4, 7),
                    color=random.choice(_SPARK_COLORS),
                )
            self._spawn_timers["spark"] = random.uniform(0.25, 0.55)

        self._blink_timer -= dt
        if self._blink_timer <= 0:
            self._blink = not self._blink
            self._blink_timer = 0.12 if self._blink else random.uniform(2.0, 4.0)

    def _spawn_confetti(self) -> None:
        w = max(self._canvas_w, 60)
        h = max(self._canvas_h, 60)
        cx, cy = self._robot_center(w, h)
        for _ in range(CONFETTI_MAX):
            slot = self._acquire(self._confetti_pool)
            if slot is None:
                break
            slot.update(
                active=True,
                x=cx + random.uniform(-12, 12), y=cy - 24,
                vx=random.uniform(-90, 90), vy=random.uniform(-170, -70),
                rot=random.uniform(0, math.pi * 2), vrot=random.uniform(-6, 6),
                size=random.uniform(4, 7), color=random.choice(_CONFETTI_COLORS),
            )

    def _update_particles(self, dt: float) -> None:
        for slot in self._dust_pool + self._file_pool + self._spark_pool:
            if not slot["active"]:
                continue
            slot["life"] += dt
            if slot["life"] >= slot["max_life"]:
                slot["active"] = False

        limit_y = self._canvas_h + 30
        for slot in self._confetti_pool:
            if not slot["active"]:
                continue
            slot["vy"] += 220 * dt
            slot["x"] += slot["vx"] * dt
            slot["y"] += slot["vy"] * dt
            slot["rot"] += slot["vrot"] * dt
            if slot["y"] > limit_y:
                slot["active"] = False

    # ---------------------------------------------------------- drawing

    def _render_scene(self) -> None:
        w = max(self._canvas_w, 60)
        h = max(self._canvas_h, 60)
        self._render_robot(w, h)
        self._render_particles()

    def _render_particles(self) -> None:
        c = self.canvas

        for slot in self._dust_pool:
            item = slot["item"]
            if not slot["active"]:
                c.itemconfigure(item, state="hidden")
                continue
            frac = _ease_out_quad(slot["life"] / slot["max_life"])
            r = slot["size"] * (1 - frac)
            if r <= 0.4:
                c.itemconfigure(item, state="hidden")
                continue
            x, y = slot["x"], slot["y"] - frac * 14
            c.coords(item, x - r, y - r, x + r, y + r)
            c.itemconfigure(item, fill=slot["color"], state="normal")

        for slot in self._file_pool:
            item = slot["item"]
            if not slot["active"]:
                c.itemconfigure(item, state="hidden")
                continue
            frac = slot["life"] / slot["max_life"]
            s = slot["size"] * (1 - frac * 0.9)
            if s <= 1:
                c.itemconfigure(item, state="hidden")
                continue
            x, y = slot["x"], slot["y"] - frac * 20
            fold = s * 0.35
            c.coords(
                item,
                x - s, y - s, x + s - fold, y - s, x + s, y - s + fold,
                x + s, y + s, x - s, y + s,
            )
            c.itemconfigure(item, fill=slot["color"], state="normal")

        for slot in self._spark_pool:
            item = slot["item"]
            if not slot["active"]:
                c.itemconfigure(item, state="hidden")
                continue
            frac = slot["life"] / slot["max_life"]
            s = slot["size"] * math.sin(min(frac, 1.0) * math.pi)
            if s <= 0.3:
                c.itemconfigure(item, state="hidden")
                continue
            x, y = slot["x"], slot["y"]
            c.coords(item, x, y - s, x + s, y, x, y + s, x - s, y)
            c.itemconfigure(item, fill=slot["color"], state="normal")

        for slot in self._confetti_pool:
            item = slot["item"]
            if not slot["active"]:
                c.itemconfigure(item, state="hidden")
                continue
            s = slot["size"]
            rot = slot["rot"]
            dx, dy = math.cos(rot) * s, math.sin(rot) * s
            dx2 = math.cos(rot + math.pi / 2) * s * 0.5
            dy2 = math.sin(rot + math.pi / 2) * s * 0.5
            x, y = slot["x"], slot["y"]
            c.coords(
                item,
                x - dx - dx2, y - dy - dy2, x + dx - dx2, y + dy - dy2,
                x + dx + dx2, y + dy + dy2, x - dx + dx2, y - dy + dy2,
            )
            c.itemconfigure(item, fill=slot["color"], state="normal")

    def _render_robot(self, w: float, h: float) -> None:
        c = self.canvas
        items = self._items
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
        c.coords(items["shadow"], cx - sw, ground_y + 2, cx + sw, ground_y + 9)

        for dx, key in ((-15, "wheel_l"), (15, "wheel_r")):
            c.coords(
                items[key],
                cx + dx - 7, cy + radius - 6, cx + dx + 7, cy + radius + 7,
            )

        c.coords(items["body"], cx - radius, cy - radius, cx + radius, cy + radius)

        c.coords(items["antenna_line"], cx, cy - radius, cx, cy - radius - 12)
        light_r = 4 + math.sin(t * 6.0) * 1.3
        light_color = _ACCENT_GREEN if state != "shrug" else _ACCENT_ORANGE
        c.coords(
            items["antenna_light"],
            cx - light_r, cy - radius - 12 - light_r, cx + light_r, cy - radius - 12 + light_r,
        )
        c.itemconfigure(items["antenna_light"], fill=light_color)

        self._render_arms_and_tool(cx, cy, radius, state, t)

        panel_w, panel_h = 30, 20
        py = cy - 4
        c.coords(items["screen"], cx - panel_w / 2, py - panel_h / 2, cx + panel_w / 2, py + panel_h / 2)

        eye_h = 1 if self._blink and state != "shrug" else 4
        for dx, key in ((-7, "eye_l"), (7, "eye_r")):
            ex, ey = cx + dx, py - 3
            c.coords(items[key], ex - 4, ey - eye_h, ex + 4, ey + eye_h)

        if state == "shrug":
            c.itemconfigure(items["mouth_arc"], state="hidden")
            c.coords(items["mouth_line"], cx - 6, py + 6, cx + 6, py + 6)
            c.itemconfigure(items["mouth_line"], state="normal")
        elif state == "finishing":
            c.coords(items["mouth_arc"], cx - 8, py - 1, cx + 8, py + 11)
            c.itemconfigure(items["mouth_arc"], state="normal")
            c.itemconfigure(items["mouth_line"], state="hidden")
        else:
            c.coords(items["mouth_arc"], cx - 6, py + 1, cx + 6, py + 8)
            c.itemconfigure(items["mouth_arc"], state="normal")
            c.itemconfigure(items["mouth_line"], state="hidden")

    def _hide_tool_items(self) -> None:
        c = self.canvas
        items = self._items
        c.itemconfigure(items["broom_handle"], state="hidden")
        c.itemconfigure(items["broom_head"], state="hidden")
        c.itemconfigure(items["broom_arc"], state="hidden")
        c.itemconfigure(items["antenna_stick"], state="hidden")
        c.itemconfigure(items["antenna_tip"], state="hidden")
        for arc in items["signal_arcs"]:
            c.itemconfigure(arc, state="hidden")

    def _render_arms_and_tool(self, cx: float, cy: float, radius: float, state: str, t: float) -> None:
        c = self.canvas
        items = self._items

        if state == "shrug":
            for side, key in ((-1, "left_arm"), (1, "right_arm")):
                sx, sy = cx + side * (radius - 6), cy + 6
                ex, ey = sx + side * 14, sy - 18
                c.coords(items[key], sx, sy, ex, ey)
            self._hide_tool_items()
            return

        lx, ly = cx - (radius - 6), cy + 6
        c.coords(items["left_arm"], lx, ly, lx - 9, ly + 10)

        rx, ry = cx + (radius - 6), cy + 6

        if self._tool == "scan":
            c.itemconfigure(items["broom_handle"], state="hidden")
            c.itemconfigure(items["broom_head"], state="hidden")
            c.itemconfigure(items["broom_arc"], state="hidden")

            angle = math.radians(80 + math.sin(t * 1.6) * 6)
            hand_x = rx + math.cos(angle) * 20
            hand_y = ry - math.sin(angle) * 20
            c.coords(items["right_arm"], rx, ry, hand_x, hand_y)

            tip_x = rx + math.cos(angle) * 34
            tip_y = ry - math.sin(angle) * 34
            c.coords(items["antenna_stick"], hand_x, hand_y, tip_x, tip_y)
            c.itemconfigure(items["antenna_stick"], state="normal")
            c.coords(items["antenna_tip"], tip_x - 5, tip_y - 5, tip_x + 5, tip_y + 5)
            c.itemconfigure(items["antenna_tip"], state="normal")

            if state == "running":
                for i, arc in enumerate(items["signal_arcs"]):
                    phase = ((t * 1.3) + i / 3) % 1.0
                    if phase >= 0.92:
                        c.itemconfigure(arc, state="hidden")
                        continue
                    r = 6 + phase * 22
                    c.coords(arc, tip_x - r, tip_y - r, tip_x + r, tip_y + r)
                    c.itemconfigure(arc, state="normal")
            else:
                for arc in items["signal_arcs"]:
                    c.itemconfigure(arc, state="hidden")
            return

        c.itemconfigure(items["antenna_stick"], state="hidden")
        c.itemconfigure(items["antenna_tip"], state="hidden")
        for arc in items["signal_arcs"]:
            c.itemconfigure(arc, state="hidden")

        if state == "running":
            angle = math.radians(45 + math.sin(t * 5.0) * 28)
        else:
            angle = math.radians(50)

        hand_x = rx + math.cos(angle) * 18
        hand_y = ry - math.sin(angle) * 18
        c.coords(items["right_arm"], rx, ry, hand_x, hand_y)

        tip_x = rx + math.cos(angle) * 40
        tip_y = ry - math.sin(angle) * 40
        c.coords(items["broom_handle"], hand_x, hand_y, tip_x, tip_y)
        c.itemconfigure(items["broom_handle"], state="normal")

        perp = angle + math.pi / 2
        spread = 9
        base_x = hand_x + (tip_x - hand_x) * 0.7
        base_y = hand_y + (tip_y - hand_y) * 0.7
        p1 = (tip_x + math.cos(perp) * spread, tip_y - math.sin(perp) * spread)
        p2 = (tip_x - math.cos(perp) * spread, tip_y + math.sin(perp) * spread)
        c.coords(items["broom_head"], base_x, base_y, p1[0], p1[1], tip_x, tip_y, p2[0], p2[1])
        c.itemconfigure(items["broom_head"], state="normal")

        if state == "running":
            deg = math.degrees(angle)
            c.coords(items["broom_arc"], tip_x - 13, tip_y - 13, tip_x + 13, tip_y + 13)
            c.itemconfigure(items["broom_arc"], start=deg - 35, state="normal")
        else:
            c.itemconfigure(items["broom_arc"], state="hidden")
