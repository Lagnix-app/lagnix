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

from core import monitor as monitor_core
from core.logging_setup import get_logger
from core.settings import load_settings
from core.system_processes import is_protected
from ui import theme

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


def _blend(bg: str, fg: str, t: float, widget: tk.Widget) -> str:
    """Змішує bg і fg (t=0 -> bg, t=1 -> fg) — для градієнтної заливки без альфи."""
    r1, g1, b1 = widget.winfo_rgb(bg)
    r2, g2, b2 = widget.winfo_rgb(fg)
    r = round((r1 + (r2 - r1) * t) / 256)
    g = round((g1 + (g2 - g1) * t) / 256)
    b = round((b1 + (b2 - b1) * t) / 256)
    return f"#{r:02x}{g:02x}{b:02x}"


def _fmt_rate(mb_per_s: float) -> str:
    if mb_per_s < 1.0:
        return f"{mb_per_s * 1024:.0f} КБ/с"
    return f"{mb_per_s:.1f} МБ/с"


# ----------------------------------------------------------------- ring gauge

class RingGauge(ctk.CTkFrame):
    """Кільце, що плавно заповнюється, з великою цифрою % посередині."""

    def __init__(self, master, title: str):
        super().__init__(master, corner_radius=14)

        self._percent_anim = theme.ValueAnimator(self, self._on_percent_step)
        self._percent_anim.current = 0.0
        self._unavailable = False

        ctk.CTkLabel(self, text=title, font=theme.font_header()).pack(pady=(16, 4))

        self.canvas = tk.Canvas(
            self, width=RING_SIZE, height=RING_SIZE, bg=theme.BG_PANEL, highlightthickness=0,
        )
        self.canvas.pack(padx=16)

        m = RING_THICKNESS
        self.canvas.create_oval(m, m, RING_SIZE - m, RING_SIZE - m, outline=theme.BORDER, width=RING_THICKNESS)
        self._fg_ring = self.canvas.create_arc(
            m, m, RING_SIZE - m, RING_SIZE - m, start=90, extent=0,
            style="arc", outline=theme.ACCENT_GREEN, width=RING_THICKNESS,
        )
        self._value_text = self.canvas.create_text(
            RING_SIZE / 2, RING_SIZE / 2, text="—",
            font=("Segoe UI", 22, "bold"), fill=theme.TEXT_MAIN,
        )

        self.subtitle_label = ctk.CTkLabel(
            self, text="", text_color=theme.TEXT_DIM, font=theme.font_small(),
            wraplength=RING_SIZE + 24, justify="center",
        )
        self.subtitle_label.pack(pady=(6, 16))

    def set_value(self, percent: float, subtitle: str) -> None:
        self._unavailable = False
        self._percent_anim.animate_to(max(0.0, min(percent, 100.0)), duration=0.3)
        self.subtitle_label.configure(text=subtitle)

    def set_unavailable(self, subtitle: str) -> None:
        self._unavailable = True
        self._percent_anim.animate_to(0.0, duration=0.3)
        self.subtitle_label.configure(text=subtitle)

    def _on_percent_step(self, value: float) -> None:
        extent = -(value / 100.0) * 360.0
        color = theme.BORDER if self._unavailable else _level_color(value)
        self.canvas.itemconfig(self._fg_ring, extent=extent, outline=color)
        text = "—" if self._unavailable else f"{value:.0f}%"
        self.canvas.itemconfig(self._value_text, text=text, fill=theme.TEXT_DIM if self._unavailable else theme.TEXT_MAIN)


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
    градієнтною заливкою під лініями."""

    SERIES = (("cpu", "CPU", theme.ACCENT_BLUE), ("gpu", "GPU", "#c77dff"), ("ram", "RAM", theme.ACCENT_GREEN))
    BANDS = 5

    def __init__(self, master):
        super().__init__(master, corner_radius=14)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 4))
        ctk.CTkLabel(header, text="Навантаження за останні 60 с", font=theme.font_header()).pack(side="left")

        legend = ctk.CTkFrame(header, fg_color="transparent")
        legend.pack(side="right")
        for _key, label, color in self.SERIES:
            item = ctk.CTkFrame(legend, fg_color="transparent")
            item.pack(side="left", padx=(14, 0))
            dot = tk.Canvas(item, width=10, height=10, highlightthickness=0, bg=theme.BG_PANEL)
            dot.create_oval(1, 1, 9, 9, fill=color, outline="")
            dot.pack(side="left", padx=(0, 5))
            ctk.CTkLabel(item, text=label, font=theme.font_small(), text_color=theme.TEXT_DIM).pack(side="left")

        self.canvas = tk.Canvas(self, height=210, bg=theme.BG_PANEL, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=16, pady=(6, 16))
        self.canvas.bind("<Configure>", lambda _e: self._redraw())

        self.history = {key: deque([0.0] * GRAPH_POINTS, maxlen=GRAPH_POINTS) for key, _, _ in self.SERIES}

    def push(self, cpu: float, gpu: float | None, ram: float) -> None:
        self.history["cpu"].append(max(0.0, min(cpu, 100.0)))
        self.history["gpu"].append(max(0.0, min(gpu, 100.0)) if gpu is not None else 0.0)
        self.history["ram"].append(max(0.0, min(ram, 100.0)))
        self._redraw()

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        width = c.winfo_width()
        height = c.winfo_height()
        if width <= 1 or height <= 1:
            return

        for frac, label in ((0.0, "0%"), (0.5, "50%"), (1.0, "100%")):
            y = height - frac * height
            c.create_line(0, y, width, y, fill=theme.BORDER, width=1)
            c.create_text(4, max(6, min(y - 8, height - 12)), text=label, anchor="w", fill=theme.TEXT_DIM, font=("Segoe UI", 8))

        series_points = {}
        for key, _label, _color in self.SERIES:
            values = self.history[key]
            n = len(values)
            step = width / (n - 1) if n > 1 else width
            series_points[key] = [(i * step, height - (v / 100.0) * height) for i, v in enumerate(values)]

        # Заливку série з більшим поточним значенням малюємо першою (як фон),
        # інакше вона (маючи більшу площу) ховає під собою заливки менших
        # серій, намальовані раніше.
        by_avg_desc = sorted(
            (key for key, _label, _color in self.SERIES),
            key=lambda key: (self.history[key][-1] if self.history[key] else 0.0),
            reverse=True,
        )
        colors_by_key = {key: color for key, _label, color in self.SERIES}
        for key in by_avg_desc:
            self._draw_gradient_area(series_points[key], colors_by_key[key], height)

        for key, _label, color in self.SERIES:
            flat = []
            for x, y in series_points[key]:
                flat.extend((x, y))
            if len(flat) >= 4:
                c.create_line(*flat, fill=color, width=2, smooth=True)

    def _draw_gradient_area(self, points, color, height) -> None:
        if len(points) < 2:
            return
        top_y = min(y for _x, y in points)
        band_height = max((height - top_y) / self.BANDS, 1.0)

        for band in range(self.BANDS):
            bottom = height - band * band_height
            top = height - (band + 1) * band_height
            blend_t = 0.06 + 0.07 * band
            fill_color = _blend(theme.BG_PANEL, color, blend_t, self)

            poly = [points[0][0], bottom]
            for x, y in points:
                poly.extend((x, min(max(y, top), bottom)))
            poly.extend((points[-1][0], bottom))
            self.canvas.create_polygon(*poly, fill=fill_color, outline="")


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
        self._dot = tk.Canvas(name_frame, width=8, height=8, highlightthickness=0, bg=theme.BG_PANEL)
        self._dot_id = self._dot.create_oval(0, 0, 8, 8, fill=theme.ACCENT_BLUE, outline="")
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

    def update_data(self, pid: int, name: str, cpu_percent: float, memory_mb: float, protected: bool) -> None:
        self.pid = pid
        self.protected = protected
        self.name_label.configure(text=name, text_color=theme.TEXT_MAIN)
        self.cpu_label.configure(text=f"{cpu_percent:.1f}%")
        self.ram_label.configure(text=f"{memory_mb:.0f} МБ")

        dot_color = theme.ERROR if cpu_percent >= 50 else (theme.WARNING if cpu_percent >= 20 else theme.ACCENT_BLUE)
        self._dot.itemconfig(self._dot_id, fill=dot_color)

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
        self._dot.itemconfig(self._dot_id, fill=theme.BORDER)
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


class StatusRobot(ctk.CTkFrame):
    """Робот із настроєм і коротка фраза про стан системи."""

    def __init__(self, master):
        super().__init__(master, corner_radius=14)

        ctk.CTkLabel(self, text="Статус системи", font=theme.font_header()).pack(padx=16, pady=(18, 8))

        self.canvas = tk.Canvas(self, width=88, height=88, highlightthickness=0, bg=theme.BG_PANEL)
        self.canvas.pack(pady=(0, 10))

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

        self._build_face()
        self.bind("<Destroy>", self._on_destroy)
        self._tick()

    def _build_face(self) -> None:
        c = self.canvas
        self._antenna_tip = c.create_oval(40, 2, 48, 10, fill=theme.ACCENT_GREEN, outline="")
        c.create_line(44, 10, 44, 18, fill=theme.TEXT_DIM, width=2)
        self._head = c.create_oval(10, 16, 78, 78, fill=theme.ACCENT_BLUE, outline=theme.ACCENT_BLUE_DIM, width=2)
        c.create_rectangle(22, 32, 66, 64, fill=theme.BG_MAIN, outline="")
        self._eye_l = c.create_arc(28, 38, 42, 52, start=20, extent=140, style="arc", outline=theme.ACCENT_GREEN, width=2)
        self._eye_r = c.create_arc(46, 38, 60, 52, start=20, extent=140, style="arc", outline=theme.ACCENT_GREEN, width=2)
        self._mouth = c.create_arc(30, 46, 58, 64, start=200, extent=140, style="arc", outline=theme.ACCENT_GREEN, width=2)

    def set_mood(self, mood: str, phrase: str) -> None:
        if mood == self._mood and phrase == self.phrase_label.cget("text"):
            return
        self._mood = mood
        color = _MOOD_COLORS[mood]
        for item in (self._head,):
            self.canvas.itemconfig(item, outline=color)
        for item in (self._eye_l, self._eye_r, self._mouth):
            self.canvas.itemconfig(item, outline=color)
        self.canvas.itemconfig(self._antenna_tip, fill=color)
        self.canvas.itemconfig(self._mouth, start=200 if mood != "worried" else 20, extent=140)
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
                extent = 8 if self._blink else 140
                self.canvas.itemconfig(self._eye_l, extent=extent)
                self.canvas.itemconfig(self._eye_r, extent=extent)
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
            self.ring_gpu.set_unavailable("недоступно (nvidia-smi не знайдено)")
            self.ring_vram.set_unavailable("недоступно")
            self.tile_gpu_temp.set_value("н/д")
            self.tile_gpu_temp.set_tooltip("Відеокарта NVIDIA не знайдена або nvidia-smi недоступний — підтримується лише NVIDIA.")
        else:
            self.ring_gpu.set_value(gpu["load_percent"], gpu["name"])
            vram_percent = (gpu["mem_used_mb"] / gpu["mem_total_mb"] * 100.0) if gpu["mem_total_mb"] else 0.0
            self.ring_vram.set_value(
                vram_percent, f"{gpu['mem_used_mb'] / 1024:.1f} з {gpu['mem_total_mb'] / 1024:.1f} ГБ",
            )
            self.tile_gpu_temp.set_value(f"{gpu['temperature_c']:.0f}°C")
            self.tile_gpu_temp.set_tooltip(None)
            if gpu["temperature_c"] > threshold:
                warnings.append(f"GPU перегрівається: {gpu['temperature_c']:.0f}°C (поріг {threshold}°C)")

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
