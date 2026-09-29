"""Мультяшна анімація робота-прибиральника для довгих операцій (очищення,
видалення програм тощо) — картка з Canvas-анімацією, текстом і прогрес-баром,
яку можна перевикористовувати на різних вкладках.

Використання: створити віджет і один раз розмістити його в макеті (pack/grid),
далі керувати лише через start()/update()/finish()/hide() — сам віджет
запам'ятовує спосіб розміщення і показує/ховає себе автоматично.

Продуктивність: цикл анімації працює на ~60 кадрів/с (after(16 мс) з
корекцією під реальний час кадру), рух рахується від фактично сплиненого
часу (time.perf_counter()), а не від номера кадру, тож затримки в системі
не викликають ривків. Робот складається зі спрайтів, намальованих через
Pillow із суперсемплінгом (ui/widgets/aa.py: 4x + LANCZOS): тіло, тінь,
вогник, інструмент і хвилі сигналу малюються один раз (лінивий кеш за
квантованими параметрами пози), а кожен кадр лише перемикає/зсуває їх —
перемальовування зображення щокадру давало ~6 мс/кадр і ~6x більше CPU.
Частки (пил/файли/іскри/конфеті) лишаються canvas-фігурами, створеними один
раз, а кожен кадр лише оновлює їхні coords()/itemconfig() — canvas.delete("all")
не використовується. Усі розміри — в dp (100% масштаб), множник DPI-масштабу
застосовується при виводі.
"""

import ctypes
import math
import platform
import random
import time
import tkinter as tk
from collections import deque

import customtkinter as ctk
from PIL import Image, ImageTk

from core import sounds
from ui import theme
from ui.widgets import aa

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
_SHADOW_RGBA = (0, 0, 0, 115)
_ARC_GREY = "#4a5568"
_SPRITE_HALF_W = 100  # dp: півширина спрайт-регіону навколо робота
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

        self._scale = self._get_widget_scaling()
        self._height_dp = height
        self._canvas_w = 320  # dp
        self._canvas_h = height  # dp
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

        self._sprites: dict = {}  # ключ -> (PhotoImage, зсув x, зсув y)
        self._shown: dict = {}  # canvas-елемент -> PhotoImage, що показується зараз
        self._pos: dict = {}  # canvas-елемент -> (x, y)

        self.canvas = tk.Canvas(self, height=round(height * self._scale), bg=_CANVAS_BG, highlightthickness=0)
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
        self._canvas_w = event.width / self._scale
        self._canvas_h = event.height / self._scale
        if self._after_id is None:
            self._render_scene()

    def _set_scaling(self, new_widget_scaling, new_window_scaling):
        super()._set_scaling(new_widget_scaling, new_window_scaling)
        self._scale = new_widget_scaling
        if hasattr(self, "canvas"):
            self._sprites.clear()
            self._shown.clear()
            self._pos.clear()
            self.canvas.configure(height=round(self._height_dp * new_widget_scaling))
            self._render_scene()

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

        # робот — спрайти (див. _render_robot); порядок = z-порядок
        self._shadow_item = c.create_image(0, 0, anchor="nw", state="hidden")
        self._body_item = c.create_image(0, 0, anchor="nw", state="hidden")
        self._light_item = c.create_image(0, 0, anchor="nw", state="hidden")
        self._tool_item = c.create_image(0, 0, anchor="nw", state="hidden")
        self._signal_item = c.create_image(0, 0, anchor="nw", state="hidden")

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
        S = self._scale

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
            c.coords(item, (x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S)
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
                *(v * S for v in (
                    x - s, y - s, x + s - fold, y - s, x + s, y - s + fold,
                    x + s, y + s, x - s, y + s,
                )),
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
            c.coords(item, x * S, (y - s) * S, (x + s) * S, y * S, x * S, (y + s) * S, (x - s) * S, y * S)
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
                *(v * S for v in (
                    x - dx - dx2, y - dy - dy2, x + dx - dx2, y + dy - dy2,
                    x + dx + dx2, y + dy + dy2, x - dx + dx2, y - dy + dy2,
                )),
            )
            c.itemconfigure(item, fill=slot["color"], state="normal")

    def _sprite(self, key, box, draw):
        """Спрайт із лінивого кешу: малюється один раз (Pillow 4x -> LANCZOS),
        далі лише показується. box=(l, t, r, b) у dp відносно якоря спрайта."""
        entry = self._sprites.get(key)
        if entry is None:
            S = self._scale
            ox, oy = math.floor(box[0] * S), math.floor(box[1] * S)
            w = math.ceil(box[2] * S) - ox
            h = math.ceil(box[3] * S) - oy
            img = aa.new_layer(w, h)
            draw(aa.Painter(img, S, ox=ox, oy=oy))
            entry = (ImageTk.PhotoImage(aa.downscale(img, (w, h))), ox, oy)
            self._sprites[key] = entry
        return entry

    def _place(self, item, entry, ax: int, ay: int) -> None:
        photo, ox, oy = entry
        c = self.canvas
        if self._shown.get(item) is not photo:
            self._shown[item] = photo
            c.itemconfigure(item, image=photo, state="normal")
        pos = (ax + ox, ay + oy)
        if self._pos.get(item) != pos:
            self._pos[item] = pos
            c.coords(item, *pos)

    def _hide(self, item) -> None:
        if self._shown.get(item) is not None:
            self._shown[item] = None
            self.canvas.itemconfigure(item, state="hidden")

    def _render_robot(self, w: float, h: float) -> None:
        """Складає робота зі спрайтів: кожен кадр лише перемикає/зсуває
        заздалегідь намальовані (Pillow, суперсемплінг) частини — без
        перемальовування зображення, тож CPU майже не росте."""
        S = self._scale
        state = self._state
        t = self._elapsed
        cx = w / 2
        ground_y = h - 14

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
        ax, ay = round(cx * S), round(cy * S)

        # тінь: ширина залежить від висоти стрибка (квантуємо до 5%)
        shadow_level = round(max(0.4, 1 - bounce / 24) * 20)
        shadow = self._sprite(
            ("shadow", shadow_level), (-46, 0, 46, 11),
            lambda p, sw=44 * shadow_level / 20: p.ellipse(-sw, 2, sw, 9, fill=_SHADOW_RGBA),
        )
        self._place(self._shadow_item, shadow, ax, round(ground_y * S))

        blink = self._blink and state != "shrug"
        body = self._sprite(
            ("body", state, blink), (-46, -46, 46, 42),
            lambda p: self._draw_body(p, state, blink),
        )
        self._place(self._body_item, body, ax, ay)

        light_r = round((4 + math.sin(t * 6.0) * 1.3) * 4) / 4
        light_color = _ACCENT_GREEN if state != "shrug" else _ACCENT_ORANGE
        light = self._sprite(
            ("light", light_color, light_r), (-7, -7, 7, 7),
            lambda p: p.ellipse(-light_r, -light_r, light_r, light_r, fill=aa.rgb(light_color)),
        )
        self._place(self._light_item, light, ax, round((cy - radius - 12) * S))

        if state == "shrug":
            self._hide(self._tool_item)
            self._hide(self._signal_item)
            return

        running = state == "running"
        if self._tool == "scan":
            deg = round(80 + math.sin(t * 1.6) * 6) if running else 80
            tool_key = ("scan", deg)
        else:
            deg = round(45 + math.sin(t * 5.0) * 28) if running else 50
            tool_key = ("broom", deg, running)
        angle = math.radians(deg)
        tool = self._sprite(
            tool_key, (12, -64, 98, 18), lambda p: self._draw_tool(p, angle, running),
        )
        self._place(self._tool_item, tool, ax, ay)

        if self._tool == "scan" and running:
            # хвилі сигналу навколо кінчика антени; фаза квантується до 1/40
            phase = round(((t * 1.3) % 1.0) * 40) / 40
            tip_x = 24 + math.cos(angle) * 34
            tip_y = 6 - math.sin(angle) * 34
            signal = self._sprite(
                ("signal", phase), (-28, -28, 28, 28), lambda p: self._draw_signals(p, phase),
            )
            self._place(self._signal_item, signal, round(cx * S + tip_x * S), round(cy * S + tip_y * S))
        else:
            self._hide(self._signal_item)

    def _draw_body(self, p: "aa.Painter", state: str, blink: bool) -> None:
        """Тіло робота з центром у (0, 0): колеса, корпус, антена, руки (у
        стані «знизування» — обидві), екран, очі, рот."""
        body_dark = aa.rgb(_BODY_DARK)
        radius = 30

        for dx in (-15, 15):
            p.ellipse(dx - 7, radius - 6, dx + 7, radius + 7,
                      fill=aa.rgb(_SCREEN_BG), outline=body_dark, width=1)
        p.ellipse(-radius, -radius, radius, radius, fill=aa.rgb(_BODY_MAIN), outline=body_dark, width=2)
        p.line([(0, -radius), (0, -radius - 12)], fill=body_dark, width=2)

        if state == "shrug":
            for side in (-1, 1):
                sx, sy = side * (radius - 6), 6
                p.line([(sx, sy), (sx + side * 14, sy - 18)], fill=body_dark, width=4)
        else:
            lx, ly = -(radius - 6), 6
            p.line([(lx, ly), (lx - 9, ly + 10)], fill=body_dark, width=4)

        py = -4
        p.rect(-15, py - 10, 15, py + 10, fill=aa.rgb(_SCREEN_BG), outline=body_dark, width=1)

        eye = aa.rgb(_EYE_COLOR)
        eye_h = 1 if blink else 4
        for dx in (-7, 7):
            p.ellipse(dx - 4, py - 3 - eye_h, dx + 4, py - 3 + eye_h, fill=eye)

        if state == "shrug":
            p.line([(-6, py + 6), (6, py + 6)], fill=eye, width=2)
        elif state == "finishing":
            p.arc(-8, py - 1, 8, py + 11, start=200, extent=140, fill=eye, width=2)
        else:
            p.arc(-6, py + 1, 6, py + 8, start=200, extent=140, fill=eye, width=2)

    def _draw_tool(self, p: "aa.Painter", angle: float, running: bool) -> None:
        """Права рука з мітлою (або антеною-сканером) під кутом angle; початок
        руки — (24, 6) відносно центру тіла."""
        body_dark = aa.rgb(_BODY_DARK)
        rx, ry = 24, 6

        if self._tool == "scan":
            hand_x = rx + math.cos(angle) * 20
            hand_y = ry - math.sin(angle) * 20
            p.line([(rx, ry), (hand_x, hand_y)], fill=body_dark, width=4)
            tip_x = rx + math.cos(angle) * 34
            tip_y = ry - math.sin(angle) * 34
            p.line([(hand_x, hand_y), (tip_x, tip_y)], fill=aa.rgb(_ANTENNA_STICK), width=3)
            p.ellipse(tip_x - 5, tip_y - 5, tip_x + 5, tip_y + 5, fill=aa.rgb(_ACCENT_GREEN))
            return

        hand_x = rx + math.cos(angle) * 18
        hand_y = ry - math.sin(angle) * 18
        p.line([(rx, ry), (hand_x, hand_y)], fill=body_dark, width=4)

        tip_x = rx + math.cos(angle) * 40
        tip_y = ry - math.sin(angle) * 40
        p.line([(hand_x, hand_y), (tip_x, tip_y)], fill=aa.rgb(_BROOM_HANDLE), width=3)

        perp = angle + math.pi / 2
        spread = 9
        base_x = hand_x + (tip_x - hand_x) * 0.7
        base_y = hand_y + (tip_y - hand_y) * 0.7
        p1 = (tip_x + math.cos(perp) * spread, tip_y - math.sin(perp) * spread)
        p2 = (tip_x - math.cos(perp) * spread, tip_y + math.sin(perp) * spread)
        p.polygon([(base_x, base_y), p1, (tip_x, tip_y), p2], fill=aa.rgb(_ACCENT_ORANGE))

        if running:
            p.arc(tip_x - 13, tip_y - 13, tip_x + 13, tip_y + 13, start=math.degrees(angle) - 35,
                  extent=25, fill=aa.rgb(_ARC_GREY), width=1, round_caps=False)

    def _draw_signals(self, p: "aa.Painter", base_phase: float) -> None:
        green = aa.rgb(_ACCENT_GREEN)
        for i in range(3):
            phase = (base_phase + i / 3) % 1.0
            if phase >= 0.92:
                continue
            r = 6 + phase * 22
            p.arc(-r, -r, r, r, start=25, extent=130, fill=green, width=1, round_caps=False)
