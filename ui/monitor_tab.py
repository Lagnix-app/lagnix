"""Вкладка «Монітор» — ігровий дашборд CPU/RAM/GPU/VRAM у реальному часі,
компактні плитки диска/мережі/температур, комбінований графік навантаження,
єдиний список процесів і робот-індикатор стану системи."""

import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from collections import deque
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image, ImageChops, ImageDraw, ImageTk

from core import monitor as monitor_core
from core import process_control, process_info
from core.logging_setup import get_logger
from core.app_icons import IconLoader
from core.settings import load_settings, update_setting
from core.system_processes import is_protected
from ui import bg, theme
from ui.widgets import aa
from ui.widgets import robot as robot_view
from ui.widgets.canvas_list import PROCESS_BADGES, CanvasList, card_image, pill_image
from ui.widgets.game_widgets import ScrollPage

DEFAULT_UPDATE_INTERVAL_SEC = 1.0
# «Бракує оперативної пам'яті»: зайнято понад 85% RAM або Windows уже
# стиснула понад 1 ГБ (Memory Compression) — ознака, що пам'яті не вистачає
LOW_RAM_PERCENT = 85
LOW_RAM_COMPRESSION_MB = 1024
GRAPH_POINTS = 60
TABLE_MIN_DP = 200  # мінімальна висота картки таблиці процесів
GRAPH_MIN_DP = 220  # мінімальна висота полотна графіка; решту висоти він ділить з таблицею процесів

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
    """Не довше за «999 МБ/с»: так плитки вміщуються в один ряд."""
    if mb_per_s < 1.0:
        return f"{mb_per_s * 1024:.0f} КБ/с"
    if mb_per_s < 100.0:
        return f"{mb_per_s:.1f} МБ/с"
    if mb_per_s < 1000.0:
        return f"{mb_per_s:.0f} МБ/с"
    return f"{mb_per_s / 1024:.1f} ГБ/с"


# ----------------------------------------------------------------- ring gauge

_TEMP_HINTS = {
    "off": "Розширені датчики вимкнені. Увімкніть їх у «Налаштуваннях», щоб бачити температуру процесора.",
    "starting": "Датчики запускаються — зачекайте кілька секунд.",
}
_TEMP_HINT_DEFAULT = (
    "Не вдалося отримати температуру процесора. Можливо, не встановлено драйвер датчиків "
    "PawnIO (pawnio.eu) — після встановлення перезапустіть програму."
)


def _cpu_temp_hint(sensor_data: dict | None) -> str:
    reason = (sensor_data or {}).get("reason")
    return _TEMP_HINTS.get(reason, _TEMP_HINT_DEFAULT)


def _cpu_temp_details(s: dict) -> str:
    """Підказка при наведенні: ядра, частота, споживання, вентилятори."""
    lines = []
    for name, value in sorted(s.get("core_temps", {}).items()):
        lines.append(f"{name}: {value:.0f}°C")
    clocks = s.get("core_clocks") or {}
    if clocks:
        lines.append(f"Частота: {sum(clocks.values()) / len(clocks) / 1000:.2f} ГГц (середня)")
    if s.get("power_w") is not None:
        lines.append(f"Споживання CPU: {s['power_w']:.0f} Вт")
    for name, rpm in s.get("fans", []):
        if rpm > 0:
            lines.append(f"{name}: {rpm:.0f} об/хв")
    return "\n".join(lines) or None


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
        theme.set_text(self.subtitle_label, subtitle)

    def set_unavailable(self, subtitle: str) -> None:
        self._unavailable = True
        self._target = 0.0
        self._percent_anim.animate_to(0.0, duration=0.3)
        theme.set_text(self.subtitle_label, subtitle)

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

_TILE_VALUE_SIZE = 14


class InfoTile(ctk.CTkFrame):
    """Компактна плитка: температура, диск, мережа, час роботи."""

    def __init__(self, master, title: str, widest_value: str):
        super().__init__(master, corner_radius=10)
        self._tooltip_win = None
        self._tooltip_text = None
        self._title = title
        # найширше можливе значення — ширину плитки рахуємо за ним, а не за
        # поточним текстом, щоб розкладка не "стрибала" щосекунди
        self._widest_value = widest_value

        ctk.CTkLabel(
            self, text=title, text_color=theme.TEXT_DIM, font=theme.font_small(), anchor="w",
        ).pack(padx=12, pady=(10, 0), fill="x")

        self.value_label = ctk.CTkLabel(
            self, text="—", font=ctk.CTkFont(family="Segoe UI", size=_TILE_VALUE_SIZE, weight="bold"),
            anchor="w", justify="left",
        )
        self.value_label.pack(padx=12, pady=(2, 10), anchor="w")

        for widget in (self, self.value_label):
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

    def set_value(self, text: str, color: str | None = None) -> None:
        theme.set_text(self.value_label, text, text_color=color or theme.TEXT_MAIN)

    def required_width(self) -> int:
        """Мінімальна ширина плитки (px), за якої ні підпис, ні значення не обрізаються."""
        S = self._get_widget_scaling()
        cached = getattr(self, "_required", None)
        if cached is not None and cached[0] == S:
            return cached[1]
        small = tkfont.Font(family="Segoe UI", size=-round(11 * S))
        header = tkfont.Font(family="Segoe UI", size=-round(_TILE_VALUE_SIZE * S), weight="bold")
        text_w = max(
            small.measure(self._title),
            max(header.measure(line) for line in self._widest_value.split("\n")),
        )
        width = text_w + round(28 * S)
        self._required = (S, width)
        return width

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

    SERIES = (("cpu", "CPU", theme.ACCENT_BLUE), ("gpu", "GPU", "#c77dff"), ("ram", "RAM", theme.ACCENT_GREEN),
              ("temp", "Темп. CPU °C", "#ffb454"))
    NO_FILL = frozenset({"temp"})  # температура — лише лінія, без заливки
    OPTIONAL = frozenset({"gpu", "temp"})  # у легенді лише поки є дані (немає GPU / датчика CPU)
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
        self._hidden: set[str] = set()
        self._legend_labels = {}
        self._legend_items = {}
        for key, label, color in self.SERIES:
            item = ctk.CTkFrame(legend, fg_color="transparent")
            if key not in self.OPTIONAL:
                item.pack(side="left", padx=(14, 0))  # необов'язкові — з'являться з першими даними
            self._legend_items[key] = item
            dot = tk.Label(
                item, image=aa.dot_image(color, 10, theme.BG_PANEL, self._scale),
                bg=theme.BG_PANEL, bd=0, highlightthickness=0, cursor="hand2",
            )
            dot.pack(side="left", padx=(0, 5))
            text = ctk.CTkLabel(item, text=label, font=theme.font_small(), text_color=theme.TEXT_DIM, cursor="hand2")
            text.pack(side="left")
            self._legend_labels[key] = text
            for widget in (dot, text):
                widget.bind("<Button-1>", lambda _e, k=key: self._toggle_series(k))

        self.canvas = tk.Canvas(
            self, height=round(GRAPH_MIN_DP * self._scale), bg=theme.BG_PANEL, highlightthickness=0,
        )
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
            self.canvas.configure(height=round(GRAPH_MIN_DP * self._scale))
            font = ("Segoe UI", -round(11 * self._scale))
            for _frac, item in self._labels:
                self.canvas.itemconfigure(item, font=font)
            self._size = (0, 0)
            self._schedule_redraw()

    def _toggle_series(self, key: str) -> None:
        """Клік по пункту легенди вмикає/вимикає лінію."""
        if key in self._hidden:
            self._hidden.discard(key)
        else:
            self._hidden.add(key)
        self._legend_labels[key].configure(
            text_color=theme.TEXT_DIM if key not in self._hidden else theme.BORDER,
        )
        self._dirty = True
        self._redraw()

    def push(self, cpu: float, gpu: float | None, ram: float, temp: float | None = None) -> None:
        self.history["cpu"].append(max(0.0, min(cpu, 100.0)))
        if gpu is None:
            self.history["gpu"].clear()  # немає GPU — не малюємо оманливу лінію на 0%
        else:
            self.history["gpu"].append(max(0.0, min(gpu, 100.0)))
        self.history["ram"].append(max(0.0, min(ram, 100.0)))
        if temp is None:
            self.history["temp"].clear()
        else:
            self.history["temp"].append(max(0.0, min(temp, 100.0)))
        for key in self.OPTIONAL:
            self._show_legend(key, bool(self.history[key]))
        self._dirty = True
        self._redraw()

    def _show_legend(self, key: str, shown: bool) -> None:
        item = self._legend_items[key]
        if shown == bool(item.winfo_manager()):
            return
        if shown:
            # зберігаємо порядок SERIES: пакуємо перед першим видимим наступним пунктом
            keys = [k for k, _l, _c in self.SERIES]
            after = [self._legend_items[k] for k in keys[keys.index(key) + 1:] if self._legend_items[k].winfo_manager()]
            item.pack(side="left", padx=(14, 0), **({"before": after[0]} if after else {}))
        else:
            item.pack_forget()

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
            if n < 2 or key in self._hidden:
                continue
            pts = [(x_right - (n - 1 - i) * step, self._y(v, h)) for i, v in enumerate(values)]
            lo, hi = self._y(100.0, h), self._y(0.0, h)
            curves[key] = [(x, min(max(y, lo), hi)) for x, y in aa.catmull_rom(pts)]

        img = self._base.copy()
        colors = {key: aa.rgb(color) for key, _label, color in self.SERIES}

        # заливку серії з більшим поточним значенням малюємо першою (як фон)
        for key in sorted((k for k in curves if k not in self.NO_FILL), key=lambda k: self.history[k][-1], reverse=True):
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

_PROC_ROW_DP = 32
_KILL_W, _KILL_H = 84, 24
# праві краї колонок (dp від правого краю таблиці): CPU, RAM, дія
_COL_CPU_R, _COL_RAM_R, _COL_ACTION_W = 6 + 90 + 10 + 80 + 10, 6 + 90 + 10, 90
_ICON_DP = 16
_ARROW_W = 22  # dp: ділянка стрілки розгортання групи
_NAME_X = {"group": 48, "proc": 26, "child": 64}  # dp: початок назви за типом рядка
_DOT_X = {"proc": 8, "child": 48}


class ProcessList(CanvasList):
    """Рядки процесів на одному Canvas. Рядок — група програми (стрілка
    розгортання, іконка, «Microsoft Edge (45)», суми CPU/RAM), процес усередині
    розгорнутої групи (з відступом) або окремий процес (режим без групування).
    Кнопка «Завершити» з'являється лише при наведенні. Щосекундне оновлення
    міняє лише текст/кольори, що справді змінилися (iset)."""

    wheel_step_dp = _PROC_ROW_DP * 3
    clickable_regions = frozenset({"kill", "expand"})
    sound_regions = frozenset({"kill"})

    def __init__(self, master, on_terminate, on_expand):
        super().__init__(master, bg=theme.BG_PANEL, scrollbar_gap=4)
        self._on_terminate = on_terminate
        self._on_expand = on_expand
        self.rows: list[dict] = []
        self._photos: dict = {}
        self.icons = IconLoader(self._icon_ready_threadsafe)

    def set_rows(self, rows: list[dict]) -> None:
        count_changed = len(rows) != len(self.rows)
        self.rows = rows
        if count_changed:
            self.set_count(len(rows), keep_scroll=True)
        else:
            self.update_visible()

    def row_height_dp(self, index: int) -> float:
        return _PROC_ROW_DP

    def on_scale_changed(self) -> None:
        self._photos.clear()

    # --------------------------------------------------------- іконки

    def _icon(self, path: str | None):
        """PhotoImage іконки exe (16 dp) або None, поки не витягнута / немає."""
        if not path:
            return None
        ready, image = self.icons.get(path)
        if not ready:
            self.icons.request({"key": path, "display_icon": (path,)})
            return None
        if image is None:
            return None
        key = (path, round(self.S, 3))
        photo = self._photos.get(key)
        if photo is None:
            px = self.px(_ICON_DP)
            photo = self._photos[key] = ImageTk.PhotoImage(image.resize((px, px), Image.Resampling.LANCZOS))
        return photo

    def _icon_ready_threadsafe(self, _key: str) -> None:
        bg.ui_call(self, self.update_visible)

    # ---------------------------------------------------------- рядки

    def _kill_box(self):
        h = self.px(_KILL_H)
        y0 = (self.px(_PROC_ROW_DP) - h) // 2
        x1 = self.width - self.px(6)
        return x1 - self.px(_KILL_W), y0, x1, y0 + h

    def create_slot(self, slot) -> None:
        c = self.canvas
        opt = (slot.tag,)
        base = (slot.tag, slot.base_tag)
        it = slot.items
        it["bg"] = c.create_image(0, 0, anchor="nw", tags=opt)
        it["arrow"] = c.create_text(0, 0, anchor="center", fill=theme.TEXT_DIM, font=self.font(16, "bold"), tags=opt)
        it["icon"] = c.create_image(0, 0, anchor="w", tags=opt)
        it["dot"] = c.create_image(0, 0, anchor="w", tags=opt)
        it["name"] = c.create_text(0, 0, anchor="w", font=self.font(13), tags=base)
        it["cpu"] = c.create_text(0, 0, anchor="e", fill=theme.TEXT_MAIN, font=self.font(13), tags=base)
        it["ram"] = c.create_text(0, 0, anchor="e", fill=theme.TEXT_MAIN, font=self.font(13), tags=base)
        it["badge_bg"] = c.create_image(0, 0, anchor="w", tags=opt)
        it["badge"] = c.create_text(0, 0, anchor="center", font=self.font(10, "bold"), tags=opt)
        it["kill_bg"] = c.create_image(0, 0, anchor="nw", tags=opt)
        it["kill"] = c.create_text(0, 0, anchor="center", text="Завершити", fill="#ffffff",
                                   font=self.font(12), tags=opt)

    def bind_slot(self, slot, index: int) -> None:
        row = self.rows[index]
        kind = row["kind"]
        w = self.width
        mid = self.px(_PROC_ROW_DP) // 2
        cpu = row["cpu_percent"]

        # стрілка розгортання (лише група з кількох процесів)
        if kind == "group" and row["count"] > 1:
            self.icoords(slot, "arrow", self.px(_ARROW_W / 2), mid)
            self.iset(slot, "arrow", text="▾" if row["expanded"] else "▸", state="normal")
        else:
            self.iset(slot, "arrow", state="hidden")

        # іконка програми для групи, інакше — кольорова крапка за навантаженням CPU
        photo = self._icon(row.get("exe_path")) if kind == "group" else None
        if photo is not None:
            self.icoords(slot, "icon", self.px(_ARROW_W + 2), mid)
            self.iset(slot, "icon", image=photo, state="normal")
            self.iset(slot, "dot", state="hidden")
        else:
            self.iset(slot, "icon", state="hidden")
            dot_x = _DOT_X.get(kind, _ARROW_W + 6)
            dot_color = theme.ERROR if cpu >= 50 else (theme.WARNING if cpu >= 20 else theme.ACCENT_BLUE)
            self.icoords(slot, "dot", self.px(dot_x), mid)
            self.iset(slot, "dot", image=aa.dot_image(dot_color, 8, theme.BG_PANEL, self.S), state="normal")

        if kind == "group":
            label = f"{row['title']} ({row['count']})" if row["count"] > 1 else row["title"]
        elif kind == "child":
            label = f"{row['name']}  ·  PID {row['pid']}"
        else:
            label = row["name"]
        name_x = self.px(_NAME_X[kind])
        name_w = w - self.px(_COL_CPU_R + 56 + 8) - name_x
        badge_kind = process_info.kind_for(row["name"]) if kind != "child" else None
        badge_w = 0
        if badge_kind:
            badge_text, badge_color = PROCESS_BADGES[badge_kind]
            badge_photo, badge_w = self.badge_image(badge_text, badge_color)
        font = self.font(13)
        shown = self.truncate(label, max(name_w - badge_w - self.px(8), self.px(40)), font)
        self.icoords(slot, "name", name_x, mid)
        self.iset(slot, "name", text=shown, fill=theme.TEXT_DIM if kind == "child" else theme.TEXT_MAIN)
        if badge_kind:
            bx = name_x + self.text_width(shown, font) + self.px(8)
            self.icoords(slot, "badge_bg", bx, mid)
            self.iset(slot, "badge_bg", image=badge_photo, state="normal")
            self.icoords(slot, "badge", bx + badge_w / 2, mid)
            self.iset(slot, "badge", text=badge_text, fill=badge_color, state="normal")
        else:
            self.iset(slot, "badge_bg", state="hidden")
            self.iset(slot, "badge", state="hidden")

        self.icoords(slot, "cpu", w - self.px(_COL_CPU_R), mid)
        self.iset(slot, "cpu", text=f"{cpu:.1f}%")
        self.icoords(slot, "ram", w - self.px(_COL_RAM_R), mid)
        self.iset(slot, "ram", text=_fmt_mem(row["memory_mb"]))
        self.icoords(slot, "bg", 0, 0)
        x0, y0, x1, y1 = self._kill_box()
        self.icoords(slot, "kill_bg", x0, y0)
        self.icoords(slot, "kill", (x0 + x1) / 2, (y0 + y1) / 2)

    def hover_slot(self, slot, index: int, region) -> None:
        row = self.rows[index]
        hovered = region is not None
        if hovered:
            self.iset(slot, "bg", image=card_image(
                max(self.width, 20), self.px(_PROC_ROW_DP), self.px(8),
                theme.BG_PANEL_LIGHT, theme.BG_PANEL_LIGHT, theme.BG_PANEL,
            ))
        self.iset(slot, "bg", state="normal" if hovered else "hidden")
        show_kill = hovered and not row["protected"]
        self.iset(slot, "kill_bg", state="normal" if show_kill else "hidden",
                  image=pill_image(self.px(_KILL_W), self.px(_KILL_H),
                                   theme.ERROR if region == "kill" else "#a8283f", self.px(6)))
        self.iset(slot, "kill", state="normal" if show_kill else "hidden")
        if row["kind"] == "group" and row["count"] > 1:
            self.iset(slot, "arrow", fill=theme.TEXT_MAIN if region == "expand" else theme.TEXT_DIM)

    # ---------------------------------------------------------- події

    def hit_test(self, index: int, x: int, y: int):
        row = self.rows[index]
        if not row["protected"]:
            x0, y0, x1, y1 = self._kill_box()
            if x0 <= x < x1 and y0 <= y < y1:
                return "kill"
        if row["kind"] == "group" and row["count"] > 1 and x < self.px(_ARROW_W + 4):
            return "expand"
        return "row"

    def click(self, index: int, region: str) -> None:
        row = self.rows[index]
        if region == "kill":
            self._on_terminate(row)
        elif region == "expand":
            self._on_expand(row["key"])

    def double_click(self, index: int, region: str) -> None:
        row = self.rows[index]
        if region == "row" and row["kind"] == "group" and row["count"] > 1:
            self._on_expand(row["key"])

    def row_identity(self, index: int):
        row = self.rows[index]
        return row["kind"], row["pid"]

    def tooltip_for(self, index: int, region: str):
        if region != "row":
            return None
        row = self.rows[index]
        text = process_info.tooltip_text(row["name"], row["pid"])
        if row["kind"] == "group" and row["count"] > 1:
            text += f"\n\nПроцесів у групі: {row['count']} (стрілка ▸ — показати окремо)"
        return text


def _fmt_mem(mb: float) -> str:
    return f"{mb / 1024:.1f} ГБ" if mb >= 10 * 1024 else f"{mb:.0f} МБ"


class ProcessTable(ctk.CTkFrame):
    """Таблиця процесів: групи програм (як у Диспетчері завдань) або окремі
    процеси, сортування «за CPU / за RAM» — за сумою групи."""

    def __init__(self, master, on_terminate):
        super().__init__(master, corner_radius=14)
        self._sort_key = "cpu"
        self._last_data: tuple | None = None  # (процеси, групи)
        self._expanded: set = set()
        self._grouped = bool(load_settings().get("monitor_group_processes", True))

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(header, text="Процеси", font=theme.font_header()).pack(side="left")

        self.toggle = ctk.CTkSegmentedButton(header, values=["За CPU", "За RAM"], command=self._on_toggle)
        self.toggle.set("За CPU")
        self.toggle.pack(side="right")

        self.group_switch = ctk.CTkSwitch(
            header, text="Групувати", font=theme.font_small(), command=self._on_group_switch, width=40,
        )
        if self._grouped:
            self.group_switch.select()
        self.group_switch.pack(side="right", padx=(0, 14))

        columns = ctk.CTkFrame(self, fg_color="transparent")
        columns.pack(fill="x", padx=(22, 22))
        columns.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(columns, text="Процес", text_color=theme.TEXT_DIM, font=theme.font_small()).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(columns, text="CPU", text_color=theme.TEXT_DIM, font=theme.font_small(), width=56, anchor="e").grid(row=0, column=1, sticky="e", padx=(0, 10))
        ctk.CTkLabel(columns, text="Пам'ять", text_color=theme.TEXT_DIM, font=theme.font_small(), width=80, anchor="e").grid(row=0, column=2, sticky="e", padx=(0, 10))
        ctk.CTkLabel(columns, text="", width=_COL_ACTION_W).grid(row=0, column=3, sticky="e", padx=(0, 6))

        self.list = ProcessList(self, on_terminate, self._toggle_expand)
        self.list.canvas.configure(height=round(_PROC_ROW_DP * 5 * self.list.S))  # ≥ 5 рядків
        self.list.pack(fill="both", expand=True, padx=(16, 8), pady=(2, 14))
        self.list.set_empty_text("Завантаження…")

    def _on_toggle(self, value: str) -> None:
        self._sort_key = "cpu" if value == "За CPU" else "ram"
        self._render()

    def _on_group_switch(self) -> None:
        self._grouped = bool(self.group_switch.get())
        update_setting("monitor_group_processes", self._grouped)
        self._render()

    def _toggle_expand(self, key) -> None:
        if key in self._expanded:
            self._expanded.discard(key)
        else:
            self._expanded.add(key)
        self._render()

    def update_processes(self, processes: list[dict], groups: list[dict] | None) -> None:
        self._last_data = (processes, groups)
        self._render()

    def _render(self) -> None:
        if self._last_data is None:
            return
        processes, groups = self._last_data
        field = "cpu_percent" if self._sort_key == "cpu" else "memory_mb"
        rows: list[dict] = []
        if self._grouped and groups is not None:
            alive = set()
            for g in sorted(groups, key=lambda g: g[field], reverse=True):
                alive.add(g["key"])
                expanded = g["key"] in self._expanded and len(g["members"]) > 1
                rows.append({
                    "kind": "group", "key": g["key"], "name": g["name"], "title": g["title"],
                    "pid": g["pid"], "exe_path": g["exe_path"], "count": len(g["members"]),
                    "cpu_percent": g["cpu_percent"], "memory_mb": g["memory_mb"],
                    "protected": g["protected"], "expanded": expanded, "members": g["members"],
                })
                if expanded:
                    for p in sorted(g["members"], key=lambda p: p[field], reverse=True):
                        rows.append(dict(p, kind="child", protected=is_protected(p["name"])))
            self._expanded &= alive  # програми, що закрилися, забуваємо
        else:
            for p in sorted(processes, key=lambda p: p[field], reverse=True):
                rows.append(dict(p, kind="proc", protected=is_protected(p["name"])))
        self.list.set_rows(rows)


# ------------------------------------------------------------- status robot

# настрій стану системи -> настрій спільного робота (ui/widgets/robot.py)
_ROBOT_MOODS = {"happy": robot_view.HAPPY, "neutral": robot_view.CALM, "worried": robot_view.WORRIED}
ROBOT_SIZE = 64
STATUS_WIDTH_DP = 260


def _page_bg(widget) -> str:
    """Колір фону під карткою (перший непрозорий предок) — для tk-контейнера."""
    while widget is not None:
        if isinstance(widget, ctk.CTkBaseClass) or isinstance(widget, ctk.CTk):
            color = widget.cget("fg_color")
            color = None if color == "transparent" else widget._apply_appearance_mode(color)
        else:  # звичайний tk-віджет (напр. внутрішня рамка ScrollPage) — його фон і видно
            try:
                color = widget.cget("bg")
            except tk.TclError:
                color = None
        if color:
            r, g, b = widget.winfo_rgb(color)
            return f"#{r >> 8:02x}{g >> 8:02x}{b >> 8:02x}"
        widget = widget.master
    return theme.BG_MAIN


class StatusRobot(ctk.CTkFrame):
    """Компактна картка «Статус системи»: робот ліворуч, праворуч — заголовок,
    коротка фраза про стан і (за потреби) кнопка-дія. Лежить у звичайному
    tk-контейнері, висоту якого MonitorTab зрівнює з рядком плиток поруч
    (match_height) — картка не звисає на графік, а CTk малює її штатно."""

    def __init__(self, master):
        # контейнер (tk.Frame) задає розмір; картка заповнює його
        self.holder = tk.Frame(master, bg=_page_bg(master), highlightthickness=0, bd=0)
        super().__init__(self.holder, corner_radius=10)  # як у плиток поруч
        self.pack(fill="both", expand=True)
        self._scale = self._get_widget_scaling()
        self._target_px = 0

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.place(relx=0, rely=0.5, anchor="w", relwidth=1)
        self.body.grid_columnconfigure(1, weight=1)

        # фон робота = колір картки (у темі він може бути назвою Tk на кшталт "gray17" — переводимо в hex)
        r, g, b = self.winfo_rgb(self._apply_appearance_mode(self.cget("fg_color")))
        card_color = f"#{r >> 8:02x}{g >> 8:02x}{b >> 8:02x}"
        self.robot = robot_view.RobotView(self.body, size=ROBOT_SIZE, mood=robot_view.CALM, bg=card_color)
        self.robot.grid(row=0, column=0, rowspan=3, padx=(12, 10), pady=8)

        ctk.CTkLabel(self.body, text="Статус системи", font=ctk.CTkFont(size=13, weight="bold"),
                     anchor="w").grid(row=0, column=1, padx=(0, 12), pady=(8, 0), sticky="sw")
        self.phrase_label = ctk.CTkLabel(
            self.body, text="Збираємо дані…", font=theme.font_small(), text_color=theme.TEXT_DIM,
            wraplength=150, justify="left", anchor="w",
        )
        self.phrase_label.grid(row=1, column=1, padx=(0, 12), sticky="nw")

        # кнопка-дія під фразою (напр. «Увімкнути Ігровий режим», коли бракує RAM)
        self._action = None
        self.action_button = ctk.CTkButton(
            self.body, text="", height=26, corner_radius=8, font=theme.font_small(),
            fg_color=theme.ACCENT_GREEN, hover_color=theme.ACCENT_GREEN_DIM, text_color=theme.BG_MAIN,
            command=lambda: self._action and self._action[1](),
        )
        self._wrap_dp = None
        tk.Misc.bind(self, "<Configure>", self._on_resize, "+")
        tk.Misc.bind(self.body, "<Configure>", lambda _e: self._fit_height(), "+")

        self._mood = None

    def set_active(self, active: bool) -> None:
        """Анімація робота працює лише на видимій вкладці."""
        self.robot.set_running(active)

    def _set_scaling(self, *args, **kwargs):
        super()._set_scaling(*args, **kwargs)
        self._scale = args[0]

    def match_height(self, height_px: int) -> None:
        """Висота рядка плиток поруч (px)."""
        if height_px > 1 and height_px != self._target_px:
            self._target_px = height_px
            self._fit_height()

    def _fit_height(self) -> None:
        needed = max(self._target_px, self.body.winfo_reqheight())
        width = round(STATUS_WIDTH_DP * self._scale)
        if needed > 1 and (int(self.holder.cget("height")) != needed or int(self.holder.cget("width")) != width):
            self.holder.configure(height=needed, width=width)
            self.holder.pack_propagate(False)

    def _on_resize(self, event=None) -> None:
        width = self.winfo_width()
        if width <= 1:
            return
        wrap = max(round(width / self._scale) - ROBOT_SIZE - 40, 80)
        if wrap != self._wrap_dp:
            self._wrap_dp = wrap
            self.phrase_label.configure(wraplength=wrap)
        self._fit_action_text()

    def _fit_action_text(self) -> None:
        """Повний підпис кнопки, якщо вміщується, інакше короткий."""
        if self._action is None:
            return
        text = self._action[0]
        short = self._action[2] if len(self._action) > 2 else text
        avail = self.winfo_width() - round((ROBOT_SIZE + 40) * self._scale)
        font = tkfont.Font(family="Segoe UI", size=-round(11 * self._scale))
        needed = font.measure(text) + round(24 * self._scale)
        wanted = text if needed <= avail or avail <= 0 else short
        if self.action_button.cget("text") != wanted:
            self.action_button.configure(text=wanted)

    def set_mood(self, mood: str, phrase: str, action: tuple | None = None) -> None:
        """action — (текст кнопки, колбек[, короткий текст для вузької картки]) або None."""
        new_text = action[0] if action else None
        old_text = self._action[0] if self._action else None
        self._action = action
        if new_text != old_text:
            if new_text is None:
                self.action_button.grid_forget()
            else:
                self.action_button.configure(text=new_text)
                self.action_button.grid(row=2, column=1, padx=(0, 12), pady=(4, 8), sticky="w")
                self._fit_action_text()
        if mood == self._mood and phrase == self.phrase_label.cget("text"):
            return
        self._mood = mood
        self.robot.set_mood(_ROBOT_MOODS.get(mood, robot_view.CALM))
        theme.set_text(self.phrase_label, phrase, text_color=theme.TEXT_MAIN)


# ------------------------------------------------------------------ the tab

class MonitorTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self._stop_event = threading.Event()
        # Поки вкладку не видно, потік не обходить процеси, а UI лише дописує
        # історію графіка; останній зріз застосовується повністю при показі.
        self._visible = False
        self._last_data: dict | None = None

        # сторінка на всю висоту вкладки; якщо вікно надто низьке для кілець,
        # плиток, графіка й таблиці з їхніми мінімумами — прокручується
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.page = ScrollPage(self, fill_height=True)
        self.page.grid(row=0, column=0, sticky="nsew")
        body = self._body = self.page.inner
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(3, weight=1)

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
        label = ctk.CTkLabel(self._body, text="Монітор", font=theme.font_title())
        label.grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 4), sticky="w")

        self.warning_label = ctk.CTkLabel(
            self._body, text="", text_color=theme.ERROR, font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
        )
        self.warning_label.grid(row=1, column=0, columnspan=2, padx=20, pady=(0, 4), sticky="ew")

    def _build_rings(self):
        rings_frame = ctk.CTkFrame(self._body, fg_color="transparent")
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
        main = ctk.CTkFrame(self._body, fg_color="transparent")
        main.grid(row=3, column=0, padx=(20, 10), pady=(0, 20), sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        # графік і таблиця процесів ділять висоту, що лишилася — на
        # невисокому вікні таблиця більше не зникає за графіком (2 : 3)
        main.grid_rowconfigure(1, weight=2)
        main.grid_rowconfigure(2, weight=3)

        tiles_frame = self._tiles_frame = ctk.CTkFrame(main, fg_color="transparent")
        tiles_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.tile_gpu_temp = InfoTile(tiles_frame, "Температура GPU", "100°C")
        self.tile_cpu_temp = InfoTile(tiles_frame, "Температура CPU", "100°C")
        self.tile_disk = InfoTile(tiles_frame, "Диск", "Чит. 99.9 МБ/с\nЗап. 99.9 МБ/с")
        self.tile_network = InfoTile(tiles_frame, "Мережа", "↓ 99.9 МБ/с\n↑ 99.9 МБ/с")
        self.tile_uptime = InfoTile(tiles_frame, "Час роботи ПК", "99 дн 23 год")
        self.tile_disk.set_tooltip("Чит. — читання з дисків, Зап. — запис на диски (усі диски разом).")
        self._tiles = [self.tile_gpu_temp, self.tile_cpu_temp, self.tile_disk, self.tile_network, self.tile_uptime]
        self._tile_columns = None
        tk.Misc.bind(tiles_frame, "<Configure>", lambda _e: self._layout_tiles(), "+")
        self._layout_tiles()

        self.graph = LoadGraph(main)
        self.graph.grid(row=1, column=0, sticky="nsew", pady=(0, 10))
        # вагу рядки ділять лише понад мінімум: на невисокому вікні графік не
        # схлопується до заголовка (полотно не нижче GRAPH_MIN_DP), стискається таблиця
        self._main = main
        tk.Misc.bind(self.graph, "<Configure>", lambda _e: self._fit_graph_row(), "+")
        self.after_idle(self._fit_graph_row)

        self.process_table = ProcessTable(main, on_terminate=self._confirm_terminate)
        self.process_table.grid(row=2, column=0, sticky="nsew")

        self.status_robot = StatusRobot(self._body)
        self.status_robot.holder.grid(row=3, column=1, padx=(10, 20), pady=(0, 20), sticky="new")
        # висота картки = висота рядка плиток поруч (і коли плитки переносяться у 2 рядки)
        tk.Misc.bind(tiles_frame, "<Configure>", lambda e: self.status_robot.match_height(e.height), "+")

    def _fit_graph_row(self) -> None:
        """Мінімуми рядків: графік — не нижче GRAPH_MIN_DP, таблиця — TABLE_MIN_DP;
        понад них висоту ділять ваги (2 : 3), а бракне — сторінка прокручується."""
        S = self._get_widget_scaling()
        mins = {1: self.graph.winfo_reqheight() + round(10 * S), 2: round(TABLE_MIN_DP * S)}
        for row, minsize in mins.items():
            if self._main.grid_rowconfigure(row, "minsize") != minsize:
                self._main.grid_rowconfigure(row, minsize=minsize)
        self.page.fit_height()

    def _layout_tiles(self) -> None:
        """5 плиток в один ряд, якщо кожна вміщує свій підпис і найширше
        значення; інакше 3 або 2 колонки (плитки переносяться на новий ряд)."""
        frame = self._tiles_frame
        avail = frame.winfo_width()
        if avail <= 1:
            avail = 10 ** 6  # ще не розміщено — поки що один ряд
        gap = round(12 * frame._get_widget_scaling())
        need = max(tile.required_width() for tile in self._tiles)
        columns = next((n for n in (5, 3, 2) if (avail - gap * (n - 1)) / n >= need), 2)
        if columns == self._tile_columns:
            return
        self._tile_columns = columns
        for col in range(5):
            frame.grid_columnconfigure(col, weight=1 if col < columns else 0, uniform="tile" if col < columns else "")
        for i, tile in enumerate(self._tiles):
            row, col = divmod(i, columns)
            tile.grid(
                row=row, column=col, sticky="nsew",
                padx=(0 if col == 0 else 6, 0 if col == columns - 1 else 6), pady=(0 if row == 0 else 12, 0),
            )
        self.after_idle(self.page.fit_height)  # плитки перейшли на інший ряд — інша потрібна висота

    # -------------------------------------------------------------- worker

    def _worker_loop(self):
        try:
            monitor_core.prime()
        except Exception:
            _logger.exception("Не вдалося ініціалізувати збір даних монітора (prime)")

        while not self._stop_event.is_set():
            try:
                data = monitor_core.collect_snapshot(include_processes=self._visible)
            except Exception as exc:
                _logger.exception("Помилка збору даних монітора")
                data = {"error": str(exc)}

            if self._stop_event.is_set():
                break

            bg.ui_call(self, self._apply_snapshot, data)

            interval = load_settings().get("monitor_update_interval_s", DEFAULT_UPDATE_INTERVAL_SEC)
            self._stop_event.wait(interval)

    def _on_destroy(self, event):
        if event.widget is self:
            self._stop_event.set()

    # --------------------------------------------------------------- apply

    def on_visibility_changed(self, visible: bool) -> None:
        self._visible = visible
        self.status_robot.set_active(visible)
        if visible:
            self.after_idle(self.page.fit_height)
        if visible and self._last_data is not None:
            # зріз із фону — без процесів: таблиця оновиться наступним зрізом
            self._render_snapshot(self._last_data, push_graph=False)

    def _apply_snapshot(self, data: dict):
        if not self.winfo_exists():
            return

        if "error" in data:
            theme.set_text(self.warning_label, f" ⚠ Помилка збору даних монітора: {data['error']}")
            return

        self._last_data = data
        if not self._visible:
            # лише накопичуємо історію графіка — нічого не перемальовуємо
            gpu = data["gpu"]
            self.graph.push(data["cpu_percent"], gpu["load_percent"] if gpu else None, data["ram_percent"], data["cpu_temp"])
            return
        self._render_snapshot(data)

    def _render_snapshot(self, data: dict, push_graph: bool = True):

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
            self.tile_cpu_temp.set_value("Недоступно")
            self.tile_cpu_temp.set_tooltip(_cpu_temp_hint(data.get("cpu_sensors")))
        else:
            self.tile_cpu_temp.set_value(
                f"{cpu_temp:.0f}°C", theme.ERROR if cpu_temp > threshold else None,
            )
            self.tile_cpu_temp.set_tooltip(_cpu_temp_details(data["cpu_sensors"]))
            if cpu_temp > threshold:
                warnings.append(f"CPU перегрівається: {cpu_temp:.0f}°C (поріг {threshold}°C)")

        self.tile_disk.set_value(
            f"Чит. {_fmt_rate(data['disk_read_mb_s'])}\nЗап. {_fmt_rate(data['disk_write_mb_s'])}"
        )
        self.tile_network.set_value(
            f"↓ {_fmt_rate(data['net_down_mb_s'])}\n↑ {_fmt_rate(data['net_up_mb_s'])}"
        )
        self.tile_uptime.set_value(data["uptime_text"])

        theme.set_text(self.warning_label, (" ⚠ " + "  |  ".join(warnings)) if warnings else "")

        if push_graph:
            self.graph.push(data["cpu_percent"], gpu["load_percent"] if gpu else None, data["ram_percent"], data["cpu_temp"])
        else:
            self.graph._schedule_redraw()

        if data["processes"] is not None:
            self.process_table.update_processes(data["processes"], data.get("process_groups"))

        self._update_status_robot(data, warnings)

    def _update_status_robot(self, data: dict, warnings: list[str]) -> None:
        if warnings:
            self.status_robot.set_mood("worried", warnings[0])
            return
        compression = data.get("memory_compression_mb") or 0.0
        if data["ram_percent"] > LOW_RAM_PERCENT or compression > LOW_RAM_COMPRESSION_MB:
            details = f"RAM {data['ram_percent']:.0f}%"
            if compression > LOW_RAM_COMPRESSION_MB:
                details += f" · стиснуто {compression / 1024:.1f} ГБ"
            action = None if self._game_mode_active() else ("Увімкнути Ігровий режим", self._enable_game_mode, "Ігровий режим")
            self.status_robot.set_mood("worried", f"Бракує оперативної пам'яті\n{details}", action)
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

    def latest_snapshot(self) -> dict | None:
        """Останній зріз метрик (для підсумків ігрових сесій); оновлюється й на прихованій вкладці."""
        return self._last_data

    def _game_tab(self):
        return getattr(self.winfo_toplevel(), "tab_frames", {}).get("game_mode")

    def _game_mode_active(self) -> bool:
        tab = self._game_tab()
        return bool(tab is not None and tab.is_active())

    def _enable_game_mode(self) -> None:
        """Кнопка робота: перейти в «Ігровий режим» і ввімкнути поточний профіль
        (там же підтвердження закриття процесів профілю)."""
        tab = self._game_tab()
        if tab is None:
            return
        self.winfo_toplevel().select_tab("game_mode")
        if not tab.is_active():
            tab.enable()

    # ---------------------------------------------------------- terminate

    def _confirm_terminate(self, row: dict):
        """«Завершити» в таблиці: для групи — уся програма (усі її процеси,
        крім системних), для окремого процесу — лише він."""
        if row["kind"] == "group" and row["count"] > 1:
            members = [p for p in row["members"] if not is_protected(p["name"])]
            title = row["title"]
            question = (
                f"Завершити «{title}» повністю — усі процеси програми ({len(members)})?\n\n"
                "Незбережені дані в цій програмі буде втрачено."
            )
            anticheat = any(process_info.is_anticheat(p["name"]) for p in members)
        else:
            members = [row]
            title = row.get("title") if row["kind"] == "group" else row["name"]
            question = f"Завершити процес «{title}» (PID {row['pid']})?"
            anticheat = process_info.is_anticheat(row["name"])
        if not members:
            return
        if anticheat:
            question += f"\n\n⚠ {process_info.ANTICHEAT_WARNING}"
        action = process_control.ask_user_action(
            self, "Підтвердження", question, reason=f"Монітор → «Завершити» {title}"
        )
        if action is None:
            return

        targets = [(p["pid"], p.get("create_time")) for p in members]

        def worker():
            killed, errors = process_control.terminate_processes(targets, action, timeout=3)
            bg.ui_call(self, self._on_terminate_result, title, killed, errors)

        threading.Thread(target=worker, daemon=True).start()

    def _on_terminate_result(self, title, killed, errors):
        if not self.winfo_exists() or not errors:
            return
        shown = "\n".join(errors[:6]) + (f"\n… і ще {len(errors) - 6}" if len(errors) > 6 else "")
        messagebox.showerror(
            "Помилка",
            f"Не вдалося завершити «{title}» повністю (завершено процесів: {killed}).\n\n{shown}",
            parent=self,
        )
