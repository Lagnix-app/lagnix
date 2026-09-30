"""Оверлей поверх ігор: маленьке напівпрозоре вікно без рамки, завжди зверху —
CPU %, GPU %, RAM %, температура GPU і CPU (якщо датчик доступний).

* Дані — ті самі зрізи, що збирає «Монітор» (MonitorTab.add_snapshot_listener):
  окремого опитування немає, лічильники CPU/GPU не «діляться» між двома читачами.
* Прозорий для кліків (WS_EX_TRANSPARENT): миша проходить у гру. Поки затиснутий
  Ctrl і курсор над оверлеєм — оверлей приймає мишу, і його можна перетягнути;
  позиція зберігається в settings.json (overlay_position).
* Не забирає фокус у гри (WS_EX_NOACTIVATE), не видно на панелі завдань
  (WS_EX_TOOLWINDOW), без власника — не ховається, коли головне вікно згорнуте.
* У повноекранних іграх з exclusive fullscreen Windows не показує чужі вікна
  поверх гри — лише в безрамковому / віконному режимі (примітка в «Налаштуваннях»).

Усе в потоці інтерфейсу: таймер ~120 мс лише читає стан Ctrl (GetAsyncKeyState).

ВАЖЛИВО: іменовані шрифти (tkfont.Font) тут ніколи не переналаштовуються
(.configure). Зміна іменованого шрифту в Tk перераховує геометрію ВСІХ віджетів
програми — у CustomTkinter це лавина перемальовувань з update_idletasks(), яка
«ламала» головне вікно, поки рухали повзунок прозорості. Тому шрифти кожного
розміру створюються один раз (_fonts_for) і лише вибираються; прозорість —
тільки attributes("-alpha") цього Toplevel (set_opacity)."""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

from core.logging_setup import get_logger
from ui import theme

_logger = get_logger(__name__)

METRICS = (  # ключ, підпис
    ("cpu", "CPU"),
    ("gpu", "GPU"),
    ("ram", "RAM"),
    ("gpu_temp", "GPU °C"),
    ("cpu_temp", "CPU °C"),
)
DEFAULT_METRICS = {key: True for key, _label in METRICS}
CORNERS = ("top_left", "top_right", "bottom_left", "bottom_right")

_SIZES = {"small": 11, "medium": 14}  # розмір шрифту в пікселях при 100%
_KEY_COLOR = "#010203"  # колір «дірок» (заокруглені кути) — повністю прозорий
_BG = theme.BG_MAIN
_POLL_MS = 120
_TOPMOST_EVERY = 16  # раз на ~2 с повторно ставимо поверх усіх (ігри теж люблять topmost)
_LOAD_WARN, _LOAD_HIGH = 75, 90

_IS_WIN = sys.platform == "win32"
if _IS_WIN:
    _user32 = ctypes.WinDLL("user32")
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
    _user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, wintypes.UINT)
    _user32.GetAsyncKeyState.restype = ctypes.c_short
_GWL_EXSTYLE, _GWLP_HWNDPARENT = -20, -8
_WS_EX_TRANSPARENT, _WS_EX_TOOLWINDOW, _WS_EX_LAYERED, _WS_EX_NOACTIVATE = 0x20, 0x80, 0x80000, 0x08000000
_HWND_TOPMOST = -1
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOACTIVATE, _SWP_FRAMECHANGED = 0x1, 0x2, 0x10, 0x20
_VK_CONTROL = 0x11
_SM_XVIRTUALSCREEN, _SM_YVIRTUALSCREEN, _SM_CXVIRTUALSCREEN, _SM_CYVIRTUALSCREEN = 76, 77, 78, 79


_font_cache: dict[int, tuple] = {}  # px -> (жирний для значень, звичайний для підписів)


def _fonts_for(widget, px: int) -> tuple:
    """Шрифти потрібного розміру: створюються один раз і ніколи не змінюються й не
    видаляються (див. примітку вгорі про іменовані шрифти)."""
    fonts = _font_cache.get(px)
    if fonts is None:
        fonts = (tkfont.Font(root=widget, family="Segoe UI", size=-px, weight="bold"),
                 tkfont.Font(root=widget, family="Segoe UI", size=-px))
        _font_cache[px] = fonts
    return fonts


def _ctrl_down() -> bool:
    return _IS_WIN and bool(_user32.GetAsyncKeyState(_VK_CONTROL) & 0x8000)


def _virtual_screen(widget) -> tuple[int, int, int, int]:
    """(x, y, ширина, висота) усіх моніторів разом."""
    if _IS_WIN:
        m = _user32.GetSystemMetrics
        w, h = m(_SM_CXVIRTUALSCREEN), m(_SM_CYVIRTUALSCREEN)
        if w > 0 and h > 0:
            return m(_SM_XVIRTUALSCREEN), m(_SM_YVIRTUALSCREEN), w, h
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


def load_color(value: float) -> str:
    if value >= _LOAD_HIGH:
        return theme.ERROR
    if value >= _LOAD_WARN:
        return theme.WARNING
    return theme.ACCENT_GREEN


def temp_color(value: float, threshold: float) -> str:
    if value >= threshold:
        return theme.ERROR
    if value >= threshold - 10:
        return theme.WARNING
    return theme.ACCENT_GREEN


def rows_from_snapshot(data: dict | None, metrics: dict) -> list[tuple[str, str, str]]:
    """[(підпис, значення, колір)] — лише увімкнені й доступні показники.
    Температура CPU без датчика не показується взагалі («якщо доступна»)."""
    if not data or "error" in data:
        return [(label, "…", theme.TEXT_DIM) for key, label in METRICS if metrics.get(key) and key != "cpu_temp"]
    threshold = data.get("temp_threshold", 85)
    gpu = data.get("gpu")
    rows = []
    for key, label in METRICS:
        if not metrics.get(key):
            continue
        if key == "cpu":
            value = data.get("cpu_percent")
            rows.append((label, f"{value:.0f}%", load_color(value)) if value is not None
                        else (label, "н/д", theme.TEXT_DIM))
        elif key == "ram":
            value = data.get("ram_percent")
            rows.append((label, f"{value:.0f}%", load_color(value)) if value is not None
                        else (label, "н/д", theme.TEXT_DIM))
        elif key == "gpu":
            value = gpu.get("load_percent") if gpu else None
            rows.append((label, f"{value:.0f}%", load_color(value)) if value is not None
                        else (label, "н/д", theme.TEXT_DIM))
        elif key == "gpu_temp":
            value = gpu.get("temperature_c") if gpu else None
            rows.append((label, f"{value:.0f}°", temp_color(value, threshold)) if value is not None
                        else (label, "н/д", theme.TEXT_DIM))
        elif key == "cpu_temp":
            value = data.get("cpu_temp")
            if value is not None:
                rows.append((label, f"{value:.0f}°", temp_color(value, threshold)))
    return rows


class OverlayWindow(tk.Toplevel):
    """config: {size, opacity, metrics, corner, position}; on_moved(x, y) — після перетягування."""

    def __init__(self, master, config: dict, on_moved):
        super().__init__(master)
        self._on_moved = on_moved
        self._config: dict = {}
        self._data: dict | None = None
        self._rows: list = []
        self._hwnd = 0
        self._click_through = None
        self._drag_from = None
        self._poll_job = None
        self._poll_count = 0
        self._opacity: float | None = None
        self._width = self._height = 1
        self._font, self._label_font = _fonts_for(self, _SIZES["small"])

        self.withdraw()
        self.overrideredirect(True)
        self.configure(bg=_KEY_COLOR)
        try:
            self.attributes("-topmost", True)
            self.attributes("-transparentcolor", _KEY_COLOR)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(self, bg=_KEY_COLOR, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self.configure_overlay(config)
        self.deiconify()
        self.update_idletasks()
        self._init_native()
        self._poll()

    # --------------------------------------------------------------- API

    def configure_overlay(self, config: dict) -> None:
        """Оновити існуюче вікно — лише те, що змінилось. Вікно не перестворюється."""
        old = self._config
        self._config = dict(config)
        self.set_opacity(config.get("opacity", 0.85))
        resized = not old or old.get("size") != config.get("size")
        if resized:
            px = round(_SIZES.get(config.get("size"), _SIZES["small"]) * self._scale())
            self._font, self._label_font = _fonts_for(self, px)
        if resized or old.get("metrics") != config.get("metrics"):
            self._redraw(force=True)
        if resized or old.get("corner") != config.get("corner") or old.get("position") != config.get("position"):
            self._place()

    def set_opacity(self, value: float) -> None:
        """Лише прозорість цього Toplevel (ніколи не головного вікна)."""
        value = max(0.3, min(1.0, float(value)))
        if value == self._opacity:
            return
        self._opacity = value
        try:
            self.attributes("-alpha", value)
        except tk.TclError:
            pass

    def update_data(self, data: dict) -> None:
        self._data = data
        self._redraw()

    def close(self) -> None:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        try:
            self.destroy()
        except tk.TclError:
            pass

    # ----------------------------------------------------------- малювання

    def _scale(self) -> float:
        try:
            return max(1.0, self.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            return 1.0

    def _redraw(self, force: bool = False) -> None:
        rows = rows_from_snapshot(self._data, {**DEFAULT_METRICS, **(self._config.get("metrics") or {})})
        if not force and rows == self._rows:
            return
        layout_changed = force or [r[0] for r in rows] != [r[0] for r in self._rows]
        self._rows = rows
        if layout_changed:
            self._draw_all()
        else:
            for index, (_label, value, color) in enumerate(rows):
                self.canvas.itemconfigure(f"value{index}", text=value, fill=color)

    def _draw_all(self) -> None:
        c = self.canvas
        c.delete("all")
        scale = self._scale()
        pad = round(8 * scale)
        gap = round(10 * scale)
        line = self._font.metrics("linespace")
        rows = self._rows or [("PulseFPS", "", theme.TEXT_DIM)]
        label_w = max(self._label_font.measure(r[0]) for r in rows)
        value_w = max(self._font.measure(m) for m in ("100%", "100°", "н/д"))
        width = pad * 2 + label_w + gap + value_w
        height = pad * 2 + line * len(rows)
        radius = round(8 * scale)

        border = theme.ACCENT_GREEN if self._click_through is False else theme.BORDER
        self._round_rect(1, 1, width - 1, height - 1, radius, fill=_BG, outline=border, tag="frame")
        for index, (label, value, color) in enumerate(rows):
            y = pad + line * index + line / 2
            c.create_text(pad, y, text=label, anchor="w", font=self._label_font, fill=theme.TEXT_DIM)
            c.create_text(width - pad, y, text=value, anchor="e", font=self._font, fill=color,
                          tags=(f"value{index}",))
        c.configure(width=width, height=height)
        if (width, height) != (self._width, self._height):
            self._width, self._height = width, height
            if self._config.get("position") is None:
                self.after_idle(self._place)  # від кута: ширина змінилась — перерахувати
            else:
                self.geometry(f"{width}x{height}+{self.winfo_x()}+{self.winfo_y()}")

    def _round_rect(self, x0, y0, x1, y1, r, **options) -> None:
        points = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
                  x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        tag = options.pop("tag", None)
        self.canvas.create_polygon(points, smooth=True, tags=(tag,) if tag else (), **options)

    # -------------------------------------------------------------- позиція

    def _place(self) -> None:
        # розмір — з останнього малювання, без update_idletasks(): той обробив би
        # відкладені перемальовування всього застосунку посеред колбека повзунка
        if not self.winfo_exists():
            return
        width, height = self._width, self._height
        vx, vy, vw, vh = _virtual_screen(self)
        position = self._config.get("position")
        if position:
            x, y = int(position[0]), int(position[1])
        else:
            margin = round(12 * self._scale())
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            corner = self._config.get("corner", "top_left")
            x = margin if "left" in corner else sw - width - margin
            y = margin if "top" in corner else sh - height - margin - round(40 * self._scale())
        x = max(vx, min(x, vx + vw - width))
        y = max(vy, min(y, vy + vh - height))
        self.geometry(f"{width}x{height}+{x}+{y}")

    # ------------------------------------------------------------- WinAPI

    def _init_native(self) -> None:
        if not _IS_WIN:
            return
        try:
            self._hwnd = int(self.wm_frame(), 16)
        except (tk.TclError, ValueError):
            self._hwnd = 0
        if not self._hwnd:
            return
        # без власника: інакше Windows ховає оверлей разом зі згорнутим головним вікном
        _user32.SetWindowLongPtrW(self._hwnd, _GWLP_HWNDPARENT, 0)
        self._set_click_through(True)

    def _set_click_through(self, enabled: bool) -> None:
        if enabled == self._click_through:
            return
        self._click_through = enabled
        if self._hwnd:
            style = _user32.GetWindowLongPtrW(self._hwnd, _GWL_EXSTYLE)
            style |= _WS_EX_LAYERED | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE
            style = style | _WS_EX_TRANSPARENT if enabled else style & ~_WS_EX_TRANSPARENT
            _user32.SetWindowLongPtrW(self._hwnd, _GWL_EXSTYLE, style)
            self._keep_on_top(_SWP_FRAMECHANGED)
        self.canvas.itemconfigure("frame", outline=theme.BORDER if enabled else theme.ACCENT_GREEN)
        self.canvas.configure(cursor="" if enabled else "fleur")

    def _keep_on_top(self, extra_flags: int = 0) -> None:
        if self._hwnd:
            _user32.SetWindowPos(self._hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                                 _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | extra_flags)

    def _pointer_inside(self) -> bool:
        try:
            px, py = self.winfo_pointerxy()
            x, y = self.winfo_rootx(), self.winfo_rooty()
            return x <= px < x + self.winfo_width() and y <= py < y + self.winfo_height()
        except tk.TclError:
            return False

    def _poll(self) -> None:
        """Ctrl + курсор над оверлеєм -> приймає мишу (можна тягнути), інакше — прозорий для кліків."""
        self._poll_job = None
        if not self.winfo_exists():
            return
        if self._drag_from is None:
            self._set_click_through(not (_ctrl_down() and self._pointer_inside()))
        self._poll_count += 1
        if self._poll_count % _TOPMOST_EVERY == 0 and self._drag_from is None:
            self._keep_on_top()
        self._poll_job = self.after(_POLL_MS, self._poll)

    # --------------------------------------------------------- перетягування

    def _on_press(self, event) -> None:
        if self._click_through:
            return
        self._drag_from = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _on_drag(self, event) -> None:
        if self._drag_from is None:
            return
        dx, dy = self._drag_from
        self.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _on_release(self, _event) -> None:
        if self._drag_from is None:
            return
        self._drag_from = None
        x, y = self.winfo_x(), self.winfo_y()
        self._config["position"] = [x, y]
        self._on_moved(x, y)
