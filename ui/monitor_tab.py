"""Вкладка «Монітор» — ігровий дашборд CPU/RAM/GPU/VRAM у реальному часі,
компактні плитки диска/мережі/температур, комбінований графік навантаження,
єдиний список процесів і робот-індикатор стану системи."""

import random
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image, ImageChops, ImageDraw, ImageTk

from core import monitor as monitor_core
from core.logging_setup import get_logger
from core.settings import load_settings
from core.system_processes import is_protected
from ui import theme
from ui.widgets import aa

DEFAULT_UPDATE_INTERVAL_SEC = 1.0
GRAPH_POINTS = 60
PROCESS_ROW_COUNT = 10

RING_SIZE = 128
RING_THICKNESS = 10

_logger = get_logger(__name__)


def _level_color(percent: float) -> str:
    if percent > 85:
        return theme.ERROR
    if percent >= 60:
        return theme.WARNING
    return theme.ACCENT_GREEN


def _fmt_rate(mb_per_s: float) -> str:
    if mb_per_s < 1.0:
        return f"{mb_per_s * 1024:.0f} КБ/с"
    return f"{mb_per_s:.1f} МБ/с"


# ----------------------------------------------------------------- ring gauge

class RingGauge(ctk.CTkFrame):
    """Кільце, що плавно заповнюється, з великою цифрою % посередині.

    Кільце малюється Pillow із суперсемплінгом (4x -> LANCZOS): незмінна
    доріжка кешується, а на кожному кроці анімації малюється лише маска дуги
    (режим L — найдешевший для ресемплінгу), яка накладається на доріжку.
    """

    RENDER_INTERVAL_S = 0.03  # анімація перемальовується не частіше ~33 р/с

    def __init__(self, master, title: str):
        super().__init__(master, corner_radius=14)

        self._percent_anim = theme.ValueAnimator(self, self._on_percent_step)
        self._percent_anim.current = 0.0
        self._unavailable = False
        self._target = 0.0
        self._last_key = None
        self._last_render = 0.0
        self._scale = self._get_widget_scaling()
        self._base = None
        self._photo = None

        ctk.CTkLabel(self, text=title, font=theme.font_header()).pack(pady=(16, 4))

        px = round(RING_SIZE * self._scale)
        self.canvas = tk.Canvas(self, width=px, height=px, bg=theme.BG_PANEL, highlightthickness=0)
        self.canvas.pack(padx=16)
        self._image_item = self.canvas.create_image(0, 0, anchor="nw")
        self._value_text = self.canvas.create_text(
            px / 2, px / 2, text="—",
            font=("Segoe UI", -round(29 * self._scale), "bold"), fill=theme.TEXT_MAIN,
        )
        self._build_base()

        self.subtitle_label = ctk.CTkLabel(
            self, text="", text_color=theme.TEXT_DIM, font=theme.font_small(),
            wraplength=RING_SIZE + 24, justify="center",
        )
        self.subtitle_label.pack(pady=(6, 16))

        self.canvas.bind("<Map>", lambda _e: self._render(self._percent_anim.current or 0.0, force=True))

    def _build_base(self) -> None:
        """Незмінна доріжка кільця (кешується); ту саму PhotoImage далі лише оновлюємо."""
        S = self._scale
        px = round(RING_SIZE * S)
        layer = aa.new_layer(px, px, "RGB", aa.rgb(theme.BG_PANEL))
        m = RING_THICKNESS
        aa.Painter(layer, S).ellipse(
            m, m, RING_SIZE - m, RING_SIZE - m, outline=aa.rgb(theme.BORDER), width=RING_THICKNESS,
        )
        self._base = aa.downscale(layer, (px, px))
        self._photo = ImageTk.PhotoImage(self._base)
        self.canvas.itemconfigure(self._image_item, image=self._photo)
        self._last_key = None

    def _set_scaling(self, *args, **kwargs):
        super()._set_scaling(*args, **kwargs)
        self._scale = args[0]
        if hasattr(self, "canvas"):
            px = round(RING_SIZE * self._scale)
            self.canvas.configure(width=px, height=px)
            self.canvas.coords(self._value_text, px / 2, px / 2)
            self.canvas.itemconfigure(self._value_text, font=("Segoe UI", -round(29 * self._scale), "bold"))
            self._build_base()
            self._render(self._percent_anim.current or 0.0, force=True)

    def set_value(self, percent: float, subtitle: str) -> None:
        self._unavailable = False
        self._target = max(0.0, min(percent, 100.0))
        self._percent_anim.animate_to(self._target, duration=0.3)
        self.subtitle_label.configure(text=subtitle)

    def set_unavailable(self, subtitle: str) -> None:
        self._unavailable = True
        self._target = 0.0
        self._percent_anim.animate_to(0.0, duration=0.3)
        self.subtitle_label.configure(text=subtitle)

    def _on_percent_step(self, value: float) -> None:
        now = time.perf_counter()
        if abs(value - self._target) > 1e-6 and now - self._last_render < self.RENDER_INTERVAL_S:
            return
        self._last_render = now
        self._render(value)

    def _render(self, value: float, force: bool = False) -> None:
        if not self.canvas.winfo_ismapped():
            self._last_key = None  # перемалюємо, коли вкладку знову покажуть (<Map>)
            return
        color = theme.BORDER if self._unavailable else _level_color(value)
        key = (round(value * 4), color, self._unavailable)
        if key == self._last_key and not force:
            return
        self._last_key = key

        S = self._scale
        px = round(RING_SIZE * S)
        out = self._base.copy()
        if value >= 0.05 and not self._unavailable:
            m = RING_THICKNESS
            mask = aa.new_layer(px, px, "L", 0)
            aa.Painter(mask, S).arc(
                m, m, RING_SIZE - m, RING_SIZE - m, start=90, extent=-(value / 100.0) * 360.0,
                fill=255, width=RING_THICKNESS,
            )
            out.paste(aa.rgb(color), (0, 0, px, px), aa.downscale(mask, (px, px)))
        self._photo.paste(out)

        text = "—" if self._unavailable else f"{value:.0f}%"
        self.canvas.itemconfigure(
            self._value_text, text=text, fill=theme.TEXT_DIM if self._unavailable else theme.TEXT_MAIN,
        )


# -------------------------------------------------------------------- tiles

class InfoTile(ctk.CTkFrame):
    """Компактна плитка: температура, диск, мережа, час роботи."""

    def __init__(self, master, title: str):
        super().__init__(master, corner_radius=10)
        self._tooltip_win = None
        self._tooltip_text = None

        ctk.CTkLabel(
            self, text=title, text_color=theme.TEXT_DIM, font=theme.font_small(), anchor="w",
        ).pack(padx=12, pady=(10, 0), fill="x")

        self.value_label = ctk.CTkLabel(
            self, text="—", font=theme.font_header(), anchor="w", justify="left",
        )
        self.value_label.pack(padx=12, pady=(2, 10), anchor="w")

        for widget in (self, self.value_label):
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

    def set_value(self, text: str) -> None:
        self.value_label.configure(text=text, text_color=theme.TEXT_MAIN)

    def set_tooltip(self, text: str | None) -> None:
        self._tooltip_text = text

    def _on_enter(self, event) -> None:
        if not self._tooltip_text:
            return
        self._hide_tooltip()
        tip = tk.Toplevel(self)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{event.x_root + 12}+{event.y_root + 18}")
        tk.Label(
            tip, text=self._tooltip_text, background="#1a1a1a", foreground="#dce4ee",
            font=("Segoe UI", 10), padx=8, pady=4, relief="solid", borderwidth=1,
            wraplength=240, justify="left",
        ).pack()
        self._tooltip_win = tip

    def _on_leave(self, _event=None) -> None:
        self._hide_tooltip()

    def _hide_tooltip(self) -> None:
        if self._tooltip_win is not None:
            self._tooltip_win.destroy()
            self._tooltip_win = None


# ------------------------------------------------------------ combined graph

class LoadGraph(ctk.CTkFrame):
    """Один гладкий графік CPU/GPU/RAM за останні 60 с, з легендою й
    градієнтною заливкою під лініями.

    Малюється Pillow: сітка (незмінна, кешується разом із фоном), для кожної
    серії — маски заливки й лінії у 4x розмірі (згладжена крива Catmull-Rom,
    ~2 px), що зменшуються через LANCZOS; заливка — плавний вертикальний
    градієнт прозорості. Історія росте справа наліво без «нулів» зліва.
    """

    SERIES = (("cpu", "CPU", theme.ACCENT_BLUE), ("gpu", "GPU", "#c77dff"), ("ram", "RAM", theme.ACCENT_GREEN))
    FILL_ALPHA = 0.42  # прозорість заливки біля лінії (далі згасає до 0 донизу)
    LINE_WIDTH = 2.0
    PAD = 4  # dp: відступ по вертикалі, щоб лінія на 0%/100% не обрізалась

    def __init__(self, master):
        super().__init__(master, corner_radius=14)
        self._scale = self._get_widget_scaling()

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 4))
        ctk.CTkLabel(header, text="Навантаження за останні 60 с", font=theme.font_header()).pack(side="left")

        legend = ctk.CTkFrame(header, fg_color="transparent")
        legend.pack(side="right")
        for _key, label, color in self.SERIES:
            item = ctk.CTkFrame(legend, fg_color="transparent")
            item.pack(side="left", padx=(14, 0))
            dot = tk.Label(
                item, image=aa.dot_image(color, 10, theme.BG_PANEL, self._scale),
                bg=theme.BG_PANEL, bd=0, highlightthickness=0,
            )
            dot.pack(side="left", padx=(0, 5))
            ctk.CTkLabel(item, text=label, font=theme.font_small(), text_color=theme.TEXT_DIM).pack(side="left")

        self.canvas = tk.Canvas(self, height=round(210 * self._scale), bg=theme.BG_PANEL, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=16, pady=(6, 16))
        self._image_item = self.canvas.create_image(0, 0, anchor="nw")
        self._labels = [
            (frac, self.canvas.create_text(
                4, 0, text=text, anchor="w", fill=theme.TEXT_DIM,
                font=("Segoe UI", -round(11 * self._scale)),
            ))
            for frac, text in ((0.0, "0%"), (0.5, "50%"), (1.0, "100%"))
        ]

        self._photo = None
        self._base = None
        self._size = (0, 0)
        self._dirty = True
        self._redraw_job = None
        self.canvas.bind("<Configure>", lambda _e: self._schedule_redraw())
        self.canvas.bind("<Map>", lambda _e: self._dirty and self._schedule_redraw())

        # історія росте з нуля: поки даних менше GRAPH_POINTS, малюємо лише наявні точки
        self.history = {key: deque(maxlen=GRAPH_POINTS) for key, _, _ in self.SERIES}

    def _set_scaling(self, *args, **kwargs):
        super()._set_scaling(*args, **kwargs)
        self._scale = args[0]
        if hasattr(self, "canvas"):
            self.canvas.configure(height=round(210 * self._scale))
            font = ("Segoe UI", -round(11 * self._scale))
            for _frac, item in self._labels:
                self.canvas.itemconfigure(item, font=font)
            self._size = (0, 0)
            self._schedule_redraw()

    def push(self, cpu: float, gpu: float | None, ram: float) -> None:
        self.history["cpu"].append(max(0.0, min(cpu, 100.0)))
        if gpu is None:
            self.history["gpu"].clear()  # немає GPU — не малюємо оманливу лінію на 0%
        else:
            self.history["gpu"].append(max(0.0, min(gpu, 100.0)))
        self.history["ram"].append(max(0.0, min(ram, 100.0)))
        self._dirty = True
        self._redraw()

    def _schedule_redraw(self) -> None:
        # Configure приходить пачками при зміні розміру вікна — зливаємо в один кадр
        if self._redraw_job is None:
            self._redraw_job = self.after(30, self._redraw)

    # ---------------------------------------------------------------- drawing

    def _rebuild_base(self, w: int, h: int) -> None:
        """Фон із сіткою (незмінний, кешується до зміни розміру) і PhotoImage."""
        S = self._scale
        base = Image.new("RGB", (w, h), aa.rgb(theme.BG_PANEL))
        draw = ImageDraw.Draw(base)
        line_w = max(1, round(S))
        for frac, item in self._labels:
            y = self._y(frac * 100.0, h)
            draw.line((0, round(y), w, round(y)), fill=aa.rgb(theme.BORDER), width=line_w)
            self.canvas.coords(item, round(4 * S), max(6 * S, min(y - 8 * S, h - 12 * S)))
        self._base = base
        self._photo = ImageTk.PhotoImage(base)
        self.canvas.itemconfigure(self._image_item, image=self._photo)
        self._size = (w, h)

    def _y(self, value: float, h: int) -> float:
        pad = self.PAD * self._scale
        return h - pad - (value / 100.0) * (h - 2 * pad)

    def _redraw(self) -> None:
        self._redraw_job = None
        c = self.canvas
        if not c.winfo_ismapped():
            return  # невидима вкладка не витрачає CPU; перемалюємо на <Map>
        w, h = c.winfo_width(), c.winfo_height()
        if w <= 8 or h <= 8:
            return
        if (w, h) != self._size:
            self._rebuild_base(w, h)

        S = self._scale
        margin = 2 * S
        x_right = w - margin
        step = (x_right - margin) / (GRAPH_POINTS - 1)

        curves = {}
        for key, _label, _color in self.SERIES:
            values = self.history[key]
            n = len(values)
            if n < 2:
                continue
            pts = [(x_right - (n - 1 - i) * step, self._y(v, h)) for i, v in enumerate(values)]
            lo, hi = self._y(100.0, h), self._y(0.0, h)
            curves[key] = [(x, min(max(y, lo), hi)) for x, y in aa.catmull_rom(pts)]

        img = self._base.copy()
        colors = {key: aa.rgb(color) for key, _label, color in self.SERIES}

        # заливку серії з більшим поточним значенням малюємо першою (як фон)
        for key in sorted(curves, key=lambda k: self.history[k][-1], reverse=True):
            self._paint_fill(img, curves[key], colors[key], w, h)
        for key in curves:
            self._paint_line(img, curves[key], colors[key], w, h)

        self._photo.paste(img)
        self._dirty = False

    @staticmethod
    def _mask(pts, box, polygon: bool, width: float = 0.0) -> Image.Image:
        """Маска фігури в межах box (пікселі), намальована у 4x і зменшена LANCZOS."""
        x0, y0, x1, y1 = box
        layer = aa.new_layer(x1 - x0, y1 - y0, "L", 0)
        painter = aa.Painter(layer, 1.0, ox=x0, oy=y0)
        if polygon:
            painter.polygon(pts, fill=255)
        else:
            painter.line(pts, fill=255, width=width)
        return aa.downscale(layer, (x1 - x0, y1 - y0))

    def _paint_fill(self, img, curve, color, w: int, h: int) -> None:
        # Суперсемплінг потрібен лише вздовж самої кривої: смугу [пік..мінімум
        # кривої] малюємо у 4x -> LANCZOS, а під нею маска суцільна (255).
        # Вертикальні краї заливки вирівняні по пікселях, тож шва немає.
        x_left = round(curve[0][0])
        x_right = round(curve[-1][0])
        y_top = max(0, int(min(y for _x, y in curve)) - 2)
        y_bot = min(h, int(max(y for _x, y in curve)) + 3)
        rw, rh = x_right - x_left, h - y_top
        if rw <= 0 or rh <= 0:
            return
        poly = [(x_left, curve[0][1])] + curve + [(x_right, y_bot), (x_left, y_bot)]
        band = self._mask(poly, (x_left, y_top, x_right, y_bot), polygon=True)
        mask = Image.new("L", (rw, rh), 0)
        mask.paste(band, (0, 0))
        if y_bot < h:
            mask.paste(255, (0, y_bot - y_top, rw, rh))
        # плавний вертикальний градієнт прозорості: найгустіший біля піку серії, донизу -> 0
        alpha = _fill_gradient(rw, rh, self.FILL_ALPHA)
        img.paste(color, (x_left, y_top, x_right, h), ImageChops.multiply(mask, alpha))

    def _paint_line(self, img, curve, color, w: int, h: int) -> None:
        pad = round(self.LINE_WIDTH * self._scale) + 2
        x0 = max(0, int(curve[0][0]) - pad)
        y0 = max(0, int(min(y for _x, y in curve)) - pad)
        y1 = min(h, int(max(y for _x, y in curve)) + pad + 1)
        box = (x0, y0, w, y1)
        mask = self._mask(curve, box, polygon=False, width=self.LINE_WIDTH * self._scale)
        img.paste(color, box, mask)


_gradient_cache: dict = {}


def _fill_gradient(width: int, height: int, top_alpha: float) -> Image.Image:
    """L-маска (width x height): alpha top_alpha*255 угорі, лінійно до 0 унизу."""
    key = (width, height, top_alpha)
    grad = _gradient_cache.get(key)
    if grad is None:
        if len(_gradient_cache) > 8:
            _gradient_cache.clear()
        column = Image.linear_gradient("L").transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        column = column.point(lambda v: round(v * top_alpha))
        grad = column.resize((width, height), Image.Resampling.BILINEAR)
        _gradient_cache[key] = grad
    return grad


# ----------------------------------------------------------------- processes

class ProcessRow(ctk.CTkFrame):
    """Рядок процесу: іконка-мітка, назва, CPU %, RAM (МБ), дія — кнопка
    «Завершити» з'являється лише при наведенні на рядок."""

    def __init__(self, master, on_terminate):
        super().__init__(master, fg_color="transparent")
        self._on_terminate = on_terminate
        self.pid = None
        self.protected = False
        self._hide_job = None

        self.grid_columnconfigure(0, weight=1)

        name_frame = ctk.CTkFrame(self, fg_color="transparent")
        name_frame.grid(row=0, column=0, sticky="ew", padx=(6, 8), pady=4)
        self._dot_scale = self._get_widget_scaling()
        self._dot = tk.Label(
            name_frame, image=self._dot_image(theme.ACCENT_BLUE), bg=theme.BG_PANEL, bd=0, highlightthickness=0,
        )
        self._dot.pack(side="left", padx=(2, 8))
        self.name_label = ctk.CTkLabel(name_frame, text="Завантаження…", anchor="w", text_color=theme.TEXT_DIM)
        self.name_label.pack(side="left", fill="x", expand=True)

        self.cpu_label = ctk.CTkLabel(self, text="", width=56, anchor="e")
        self.cpu_label.grid(row=0, column=1, sticky="e", padx=(0, 10))

        self.ram_label = ctk.CTkLabel(self, text="", width=80, anchor="e")
        self.ram_label.grid(row=0, column=2, sticky="e", padx=(0, 10))

        self.action_frame = ctk.CTkFrame(self, fg_color="transparent", width=90)
        self.action_frame.grid(row=0, column=3, sticky="e", padx=(0, 6))
        self.action_frame.grid_propagate(False)

        self.kill_button = ctk.CTkButton(
            self.action_frame, text="Завершити", width=84, height=24,
            fg_color="#a8283f", hover_color=theme.ERROR, command=self._handle_click,
        )
        self.tag_label = ctk.CTkLabel(
            self.action_frame, text="системний", text_color=theme.TEXT_DIM, font=theme.font_small(),
        )

        for widget in (self, name_frame, self.name_label, self.cpu_label, self.ram_label, self.action_frame, self.kill_button):
            widget.bind("<Enter>", self._on_hover_enter)
            widget.bind("<Leave>", self._on_hover_leave)

    def _dot_image(self, color: str):
        return aa.dot_image(color, 8, theme.BG_PANEL, self._dot_scale)

    def update_data(self, pid: int, name: str, cpu_percent: float, memory_mb: float, protected: bool) -> None:
        self.pid = pid
        self.protected = protected
        self.name_label.configure(text=name, text_color=theme.TEXT_MAIN)
        self.cpu_label.configure(text=f"{cpu_percent:.1f}%")
        self.ram_label.configure(text=f"{memory_mb:.0f} МБ")

        dot_color = theme.ERROR if cpu_percent >= 50 else (theme.WARNING if cpu_percent >= 20 else theme.ACCENT_BLUE)
        self._dot.configure(image=self._dot_image(dot_color))

        self.kill_button.pack_forget()
        if protected:
            self.tag_label.pack(side="right")
        else:
            self.tag_label.pack_forget()

    def clear(self) -> None:
        self.pid = None
        self.protected = False
        self.name_label.configure(text="Завантаження…", text_color=theme.TEXT_DIM)
        self.cpu_label.configure(text="")
        self.ram_label.configure(text="")
        self._dot.configure(image=self._dot_image(theme.BORDER))
        self.tag_label.pack_forget()
        self.kill_button.pack_forget()

    def _on_hover_enter(self, _event=None) -> None:
        if self._hide_job is not None:
            try:
                self.after_cancel(self._hide_job)
            except Exception:
                pass
            self._hide_job = None
        if self.pid is not None and not self.protected:
            self.kill_button.pack(side="right")

    def _on_hover_leave(self, _event=None) -> None:
        self._hide_job = self.after(80, self._hide_kill_button)

    def _hide_kill_button(self) -> None:
        self._hide_job = None
        self.kill_button.pack_forget()

    def _handle_click(self) -> None:
        if self.pid is not None:
            self._on_terminate(self.pid, self.name_label.cget("text"))


class ProcessTable(ctk.CTkFrame):
    """Єдина таблиця процесів із перемикачем сортування «за CPU / за RAM»."""

    def __init__(self, master, on_terminate):
        super().__init__(master, corner_radius=14)
        self._sort_key = "cpu"
        self._last_processes: list[dict] | None = None

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(header, text="Процеси", font=theme.font_header()).pack(side="left")

        self.toggle = ctk.CTkSegmentedButton(header, values=["За CPU", "За RAM"], command=self._on_toggle)
        self.toggle.set("За CPU")
        self.toggle.pack(side="right")

        columns = ctk.CTkFrame(self, fg_color="transparent")
        columns.pack(fill="x", padx=(22, 22))
        columns.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(columns, text="Процес", text_color=theme.TEXT_DIM, font=theme.font_small()).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(columns, text="CPU", text_color=theme.TEXT_DIM, font=theme.font_small(), width=56, anchor="e").grid(row=0, column=1, sticky="e", padx=(0, 10))
        ctk.CTkLabel(columns, text="RAM", text_color=theme.TEXT_DIM, font=theme.font_small(), width=80, anchor="e").grid(row=0, column=2, sticky="e", padx=(0, 10))
        ctk.CTkLabel(columns, text="", width=90).grid(row=0, column=3, sticky="e", padx=(0, 6))

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.pack(fill="both", expand=True, padx=10, pady=(2, 14))

        self.rows = [ProcessRow(self.scroll, on_terminate=on_terminate) for _ in range(PROCESS_ROW_COUNT)]
        for row in self.rows:
            row.pack(fill="x", pady=1)

    def _on_toggle(self, value: str) -> None:
        self._sort_key = "cpu" if value == "За CPU" else "ram"
        if self._last_processes is not None:
            self._render(self._last_processes)

    def update_processes(self, processes: list[dict]) -> None:
        self._last_processes = processes
        self._render(processes)

    def _render(self, processes: list[dict]) -> None:
        key = (lambda p: p["cpu_percent"]) if self._sort_key == "cpu" else (lambda p: p["memory_mb"])
        ordered = sorted(processes, key=key, reverse=True)[:PROCESS_ROW_COUNT]
        for i, row in enumerate(self.rows):
            if i < len(ordered):
                p = ordered[i]
                row.update_data(
                    pid=p["pid"], name=p["name"], cpu_percent=p["cpu_percent"],
                    memory_mb=p["memory_mb"], protected=is_protected(p["name"]),
                )
            else:
                row.clear()


# ------------------------------------------------------------- status robot

_MOOD_COLORS = {"happy": theme.ACCENT_GREEN, "neutral": theme.ACCENT_BLUE, "worried": theme.WARNING}
ROBOT_SIZE = 88


class StatusRobot(ctk.CTkFrame):
    """Робот із настроєм і коротка фраза про стан системи.

    Малюється Pillow (4x -> LANCZOS) у вигляді спрайтів за парою
    (настрій, кліпання) — кожен малюється один раз і далі лише перемикається.
    """

    def __init__(self, master):
        super().__init__(master, corner_radius=14)
        self._scale = self._get_widget_scaling()
        self._sprites: dict = {}

        ctk.CTkLabel(self, text="Статус системи", font=theme.font_header()).pack(padx=16, pady=(18, 8))

        px = round(ROBOT_SIZE * self._scale)
        self.canvas = tk.Canvas(self, width=px, height=px, highlightthickness=0, bg=theme.BG_PANEL)
        self.canvas.pack(pady=(0, 10))
        self._image_item = self.canvas.create_image(0, 0, anchor="nw")

        self.phrase_label = ctk.CTkLabel(
            self, text="Збираємо дані…", font=theme.font_body(), text_color=theme.TEXT_DIM,
            wraplength=190, justify="center",
        )
        self.phrase_label.pack(padx=16, pady=(0, 20))

        self._mood = None
        self._blink = False
        self._blink_timer = random.uniform(1.6, 3.0)
        self._last_tick = None
        self._after_id = None

        self._show()
        self.bind("<Destroy>", self._on_destroy)
        self._tick()

    def _set_scaling(self, *args, **kwargs):
        super()._set_scaling(*args, **kwargs)
        self._scale = args[0]
        if hasattr(self, "canvas"):
            px = round(ROBOT_SIZE * self._scale)
            self.canvas.configure(width=px, height=px)
            self._sprites.clear()
            self._show()

    def _render(self, mood, blink: bool) -> ImageTk.PhotoImage:
        S = self._scale
        px = round(ROBOT_SIZE * S)
        color = aa.rgb(_MOOD_COLORS.get(mood, theme.ACCENT_GREEN))
        head_outline = color if mood else aa.rgb(theme.ACCENT_BLUE_DIM)

        img = aa.new_layer(px, px, "RGB", aa.rgb(theme.BG_PANEL))
        p = aa.Painter(img, S)
        p.line([(44, 10), (44, 18)], fill=aa.rgb(theme.TEXT_DIM), width=2, round_caps=False)
        p.ellipse(40, 2, 48, 10, fill=color)
        p.ellipse(10, 16, 78, 78, fill=aa.rgb(theme.ACCENT_BLUE), outline=head_outline, width=2)
        p.rect(22, 32, 66, 64, fill=aa.rgb(theme.BG_MAIN))
        eye_extent = 8 if blink else 140
        p.arc(28, 38, 42, 52, start=20, extent=eye_extent, fill=color, width=2)
        p.arc(46, 38, 60, 52, start=20, extent=eye_extent, fill=color, width=2)
        p.arc(30, 46, 58, 64, start=20 if mood == "worried" else 200, extent=140, fill=color, width=2)
        return ImageTk.PhotoImage(aa.downscale(img, (px, px)))

    def _show(self) -> None:
        key = (self._mood, self._blink)
        photo = self._sprites.get(key)
        if photo is None:
            photo = self._sprites[key] = self._render(*key)
        self.canvas.itemconfigure(self._image_item, image=photo)

    def set_mood(self, mood: str, phrase: str) -> None:
        if mood == self._mood and phrase == self.phrase_label.cget("text"):
            return
        self._mood = mood
        self._show()
        self.phrase_label.configure(text=phrase, text_color=theme.TEXT_MAIN)

    def _tick(self) -> None:
        if not self.winfo_exists():
            return
        if theme.robot_animation_enabled():
            now = time.perf_counter()
            dt = 0.0 if self._last_tick is None else now - self._last_tick
            self._last_tick = now
            self._blink_timer -= dt
            if self._blink_timer <= 0:
                self._blink = not self._blink
                self._blink_timer = random.uniform(0.12, 0.2) if self._blink else random.uniform(2.0, 4.0)
                self._show()
        self._after_id = self.after(150, self._tick)

    def _on_destroy(self, event) -> None:
        if event.widget is self and self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None


# ------------------------------------------------------------------ the tab

class MonitorTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self._stop_event = threading.Event()

        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._build_header()
        self._build_rings()
        self._build_main_and_sidebar()

        self.bind("<Destroy>", self._on_destroy)

        # Через after(0, ...), а не напряму: вкладки створюються ще до
        # MainWindow.mainloop(), і потік міг би викликати self.after() ще
        # до реального старту mainloop — з Python 3.13+ це кидає непіймане
        # RuntimeError: main thread is not in main loop, і потік мовчки
        # гине, залишаючи вкладку з "—" назавжди.
        self.after(0, self._start_worker)

    def _start_worker(self) -> None:
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    # ------------------------------------------------------------------ UI

    def _build_header(self):
        label = ctk.CTkLabel(self, text="Монітор", font=theme.font_title())
        label.grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 4), sticky="w")

        self.warning_label = ctk.CTkLabel(
            self, text="", text_color=theme.ERROR, font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
        )
        self.warning_label.grid(row=1, column=0, columnspan=2, padx=20, pady=(0, 4), sticky="ew")

    def _build_rings(self):
        rings_frame = ctk.CTkFrame(self, fg_color="transparent")
        rings_frame.grid(row=2, column=0, columnspan=2, padx=20, pady=(6, 10), sticky="ew")
        rings_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.ring_cpu = RingGauge(rings_frame, "CPU")
        self.ring_cpu.grid(row=0, column=0, padx=6, sticky="nsew")
        self.ring_ram = RingGauge(rings_frame, "RAM")
        self.ring_ram.grid(row=0, column=1, padx=6, sticky="nsew")
        self.ring_gpu = RingGauge(rings_frame, "GPU")
        self.ring_gpu.grid(row=0, column=2, padx=6, sticky="nsew")
        self.ring_vram = RingGauge(rings_frame, "VRAM")
        self.ring_vram.grid(row=0, column=3, padx=6, sticky="nsew")

    def _build_main_and_sidebar(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=3, column=0, padx=(20, 10), pady=(0, 20), sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        tiles_frame = ctk.CTkFrame(main, fg_color="transparent")
        tiles_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        tiles_frame.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        self.tile_gpu_temp = InfoTile(tiles_frame, "Температура GPU")
        self.tile_gpu_temp.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        self.tile_cpu_temp = InfoTile(tiles_frame, "Температура CPU")
        self.tile_cpu_temp.grid(row=0, column=1, padx=6, sticky="nsew")
        self.tile_disk = InfoTile(tiles_frame, "Диск")
        self.tile_disk.grid(row=0, column=2, padx=6, sticky="nsew")
        self.tile_network = InfoTile(tiles_frame, "Мережа")
        self.tile_network.grid(row=0, column=3, padx=6, sticky="nsew")
        self.tile_uptime = InfoTile(tiles_frame, "Час роботи ПК")
        self.tile_uptime.grid(row=0, column=4, padx=(6, 0), sticky="nsew")

        self.graph = LoadGraph(main)
        self.graph.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        self.process_table = ProcessTable(main, on_terminate=self._confirm_terminate)
        self.process_table.grid(row=2, column=0, sticky="nsew")

        self.status_robot = StatusRobot(self)
        self.status_robot.grid(row=3, column=1, padx=(10, 20), pady=(0, 20), sticky="new")

    # -------------------------------------------------------------- worker

    def _worker_loop(self):
        try:
            monitor_core.prime()
        except Exception:
            _logger.exception("Не вдалося ініціалізувати збір даних монітора (prime)")

        while not self._stop_event.is_set():
            try:
                data = monitor_core.collect_snapshot()
            except Exception as exc:
                _logger.exception("Помилка збору даних монітора")
                data = {"error": str(exc)}

            if self._stop_event.is_set():
                break

            try:
                self.after(0, self._apply_snapshot, data)
            except RuntimeError:
                _logger.exception("Не вдалося передати знімок монітора в UI")

            interval = load_settings().get("monitor_update_interval_s", DEFAULT_UPDATE_INTERVAL_SEC)
            self._stop_event.wait(interval)

    def _on_destroy(self, event):
        if event.widget is self:
            self._stop_event.set()

    # --------------------------------------------------------------- apply

    def _apply_snapshot(self, data: dict):
        if not self.winfo_exists():
            return

        if "error" in data:
            self.warning_label.configure(text=f" ⚠ Помилка збору даних монітора: {data['error']}")
            return

        threshold = data["temp_threshold"]
        warnings = []

        freq = data["cpu_freq_ghz"]
        freq_text = f"{freq:.2f} ГГц" if freq else "частота: н/д"
        self.ring_cpu.set_value(data["cpu_percent"], freq_text)

        self.ring_ram.set_value(
            data["ram_percent"], f"{data['ram_used_gb']:.1f} з {data['ram_total_gb']:.1f} ГБ",
        )

        gpu = data["gpu"]
        cpu_temp = data["cpu_temp"]

        if gpu is None:
            self.ring_gpu.set_unavailable("недоступно (GPU не знайдено)")
            self.ring_vram.set_unavailable("недоступно")
            self.tile_gpu_temp.set_value("н/д")
            self.tile_gpu_temp.set_tooltip("Відеокарту не знайдено: немає ні лічильників Windows «GPU Engine», ні NVML/nvidia-smi.")
        else:
            self.ring_gpu.set_value(gpu["load_percent"], gpu["name"])
            if gpu["mem_total_mb"]:
                vram_percent = gpu["mem_used_mb"] / gpu["mem_total_mb"] * 100.0
                self.ring_vram.set_value(
                    vram_percent, f"{gpu['mem_used_mb'] / 1024:.1f} з {gpu['mem_total_mb'] / 1024:.1f} ГБ",
                )
            else:
                self.ring_vram.set_unavailable("недоступно")
            temp = gpu["temperature_c"]
            if temp is None:
                self.tile_gpu_temp.set_value("н/д")
                self.tile_gpu_temp.set_tooltip("Температура GPU доступна лише для відеокарт NVIDIA.")
            else:
                self.tile_gpu_temp.set_value(f"{temp:.0f}°C")
                self.tile_gpu_temp.set_tooltip(None)
                if temp > threshold:
                    warnings.append(f"GPU перегрівається: {temp:.0f}°C (поріг {threshold}°C)")

        if cpu_temp is None:
            self.tile_cpu_temp.set_value("н/д")
            self.tile_cpu_temp.set_tooltip("Не вдалося визначити датчик температури CPU на цьому ПК (може знадобитись пакет wmi).")
        else:
            self.tile_cpu_temp.set_value(f"{cpu_temp:.0f}°C")
            self.tile_cpu_temp.set_tooltip(None)
            if cpu_temp > threshold:
                warnings.append(f"CPU перегрівається: {cpu_temp:.0f}°C (поріг {threshold}°C)")

        self.tile_disk.set_value(
            f"Читання {_fmt_rate(data['disk_read_mb_s'])}\nЗапис {_fmt_rate(data['disk_write_mb_s'])}"
        )
        self.tile_network.set_value(
            f"↓ {_fmt_rate(data['net_down_mb_s'])}\n↑ {_fmt_rate(data['net_up_mb_s'])}"
        )
        self.tile_uptime.set_value(data["uptime_text"])

        self.warning_label.configure(text=(" ⚠ " + "  |  ".join(warnings)) if warnings else "")

        self.graph.push(data["cpu_percent"], gpu["load_percent"] if gpu else None, data["ram_percent"])

        self.process_table.update_processes(data["processes"])

        self._update_status_robot(data, warnings)

    def _update_status_robot(self, data: dict, warnings: list[str]) -> None:
        if warnings:
            self.status_robot.set_mood("worried", warnings[0])
            return
        if data["ram_percent"] > 90:
            self.status_robot.set_mood("worried", "RAM майже заповнена — закрий зайві програми")
            return
        if data["cpu_percent"] > 90:
            self.status_robot.set_mood("worried", "CPU сильно завантажений")
            return

        gpu = data["gpu"]
        elevated = data["ram_percent"] > 75 or data["cpu_percent"] > 75 or (gpu and gpu["load_percent"] > 85)
        if elevated:
            self.status_robot.set_mood("neutral", "Є невелике навантаження, але все під контролем")
        else:
            self.status_robot.set_mood("happy", "Все чудово, система в нормі")

    # ---------------------------------------------------------- terminate

    def _confirm_terminate(self, pid: int, name: str):
        confirmed = messagebox.askyesno(
            "Підтвердження",
            f"Завершити процес «{name}» (PID {pid})?",
            parent=self,
        )
        if not confirmed:
            return

        def worker():
            success, error = monitor_core.terminate_process(pid)
            self.after(0, self._on_terminate_result, name, pid, success, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_terminate_result(self, name, pid, success, error):
        if not self.winfo_exists():
            return
        if not success:
            messagebox.showerror(
                "Помилка",
                f"Не вдалося завершити «{name}» (PID {pid}): {error}",
                parent=self,
            )
