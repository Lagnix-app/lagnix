"""Спільна легка прокрутка: tk.Canvas + tk.Frame усередині (замість CTkScrollableFrame).

Чому не CTkScrollableFrame: його canvas прозорий (CTk-віджети малюють кути за
кольором фону під собою), команда повзунка й коліщатко прокручують на КОЖНУ
подію, а кожен крок — це синхронне переміщення десятків вкладених вікон. При
швидкому перетягуванні повзунка події надходять частіше, ніж вікно встигає
перемалюватись, — звідси "шлейфи".

Тут:
  * ScrollController збирає зсув і застосовує його не частіше ніж раз на ~16 мс
    (після — один update_idletasks());
  * theme.notify_scroll() під час прокрутки вимикає анімації наведення й живі
    оновлення (theme.is_scrolling()), вмикаються через ~150 мс після зупинки;
  * фон canvas і внутрішньої рамки — суцільний колір, без прозорості;
  * коліщатко — одна глобальна прив'язка, що віддає подію лише тому
    прокручуваному блоку, який під курсором (і лише видимому); повзунки CTk
    (які самі міняють значення від коліщатка) від нього вимкнено."""

from __future__ import annotations

import time
import tkinter as tk

import customtkinter as ctk

from ui import theme
from ui.widgets.canvas_list import CanvasList, FastScrollbar

_FRAME_S = 0.016            # не частіше одного застосування зсуву за 16 мс
_WHEEL_PX = 84              # пікселів за один "клік" колеса (при масштабі 100%)

_registry: dict[int, "_Target"] = {}
_wheel_bound = False


class _Target:
    def __init__(self, widget: tk.Misc, controller: "ScrollController", can_scroll, scale: float):
        self.widget, self.controller, self.can_scroll, self.scale = widget, controller, can_scroll, scale


class ScrollController:
    """Збирає запити прокрутки й застосовує їх раз на кадр."""

    def __init__(self, widget: tk.Misc, canvas: tk.Canvas):
        self._widget = widget
        self._canvas = canvas
        self._pending_px = 0.0
        self._pending_frac: float | None = None
        self._job = None
        self._last_applied = 0.0

    def by_pixels(self, pixels: float) -> None:
        if self._pending_frac is not None:
            self._pending_frac = None
        self._pending_px += pixels
        self._schedule()

    def moveto(self, fraction: float) -> None:
        self._pending_frac = fraction
        self._pending_px = 0.0
        self._schedule()

    def moveto_now(self, fraction: float) -> None:
        self._pending_frac, self._pending_px = None, 0.0
        self._canvas.yview_moveto(fraction)

    def _schedule(self) -> None:
        if self._job is not None:
            return
        wait = _FRAME_S - (time.perf_counter() - self._last_applied)
        try:
            if wait <= 0:
                self._job = self._widget.after_idle(self._flush)
            else:
                self._job = self._widget.after(max(1, round(wait * 1000)), self._flush)
        except tk.TclError:
            self._job = None

    def _flush(self) -> None:
        self._job = None
        canvas = self._canvas
        try:
            frac, px = self._pending_frac, self._pending_px
            self._pending_frac, self._pending_px = None, 0.0
            if frac is None:
                region = canvas.cget("scrollregion").split()
                total = float(region[3]) - float(region[1]) if len(region) == 4 else 0.0
                if total <= 0 or px == 0:
                    return
                frac = canvas.yview()[0] + px / total
            canvas.yview_moveto(max(0.0, min(frac, 1.0)))
            theme.notify_scroll()
            canvas.update_idletasks()
            self._last_applied = time.perf_counter()
        except tk.TclError:
            pass


# ----------------------------------------------------------------- коліщатко

def _on_wheel(event) -> None:
    if not _registry:
        return
    try:
        widget = event.widget if isinstance(event.widget, tk.Misc) else None
        widget = widget.winfo_containing(event.x_root, event.y_root) if widget is not None else None
    except (tk.TclError, KeyError):
        return
    while widget is not None:
        if isinstance(widget, (CanvasList, tk.Listbox)):
            return  # віртуалізований список прокручується сам
        target = _registry.get(id(widget))
        if target is not None and target.widget is widget:
            if target.can_scroll():
                target.controller.by_pixels(-event.delta / 120 * _WHEEL_PX * target.scale)
            return
        widget = getattr(widget, "master", None)


def register(widget: tk.Misc, controller: ScrollController, can_scroll, scale: float = 1.0) -> None:
    """Підключає блок до глобального коліщатка. `widget` — контейнер, що
    містить canvas (курсор над будь-яким його нащадком прокручує саме його)."""
    global _wheel_bound
    _registry[id(widget)] = _Target(widget, controller, can_scroll, scale)
    if not _wheel_bound:
        _wheel_bound = True
        tk.Misc.bind_all(widget, "<MouseWheel>", _on_wheel, "+")  # CTk забороняє bind_all у своїх віджетах
    tk.Misc.bind(widget, "<Destroy>", lambda e, k=id(widget): _registry.pop(k, None) if e.widget is widget else None, "+")


def _slider_ignores_wheel(self, _event=None):
    """CTkSlider сам міняє значення від коліщатка: прокручуючи сторінку над
    повзунком гучності, користувач непомітно скидав її в 0."""
    return "break"


ctk.CTkSlider._mouse_scroll_event = _slider_ignores_wheel


# --------------------------------------------------------------- компонент

def widget_scale(widget: tk.Misc) -> float:
    """Масштаб CTk (DPI) для звичайного tk-віджета: береться в найближчого CTk-предка."""
    while widget is not None:
        getter = getattr(widget, "_get_widget_scaling", None)
        if getter is not None:
            return getter()
        widget = getattr(widget, "master", None)
    return 1.0


class _Outer(tk.Frame):
    dying = False

    def destroy(self):
        self.dying = True
        super().destroy()


class ScrollFrame(tk.Frame):
    """Прокручувана рамка: сам об'єкт — ВНУТРІШНЯ рамка (батько для вмісту),
    а pack/grid/place розміщують зовнішній контейнер із canvas і повзунком."""

    def __init__(self, master, *, bg: str | None = None, width: int = 0, height: int = 0,
                 scrollbar_gap: int = 2, fill_width: bool = True):
        scale = widget_scale(master)
        bg = bg or theme._bg_of(master)  # суцільний колір фону сторінки, а не прозорість
        self._outer = _Outer(master, bg=bg, bd=0, highlightthickness=0, width=width, height=height)
        if width or height:
            self._outer.grid_propagate(False)
        self._outer.grid_columnconfigure(0, weight=1)
        self._outer.grid_rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self._outer, bg=bg, highlightthickness=0, bd=0, takefocus=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = FastScrollbar(self._outer, self._on_scrollbar, bg=bg, scale=scale)
        self.scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, scrollbar_gap))
        super().__init__(self.canvas, bg=bg, bd=0, highlightthickness=0)
        self._bg = bg
        self._fill_width = fill_width
        self._window = self.canvas.create_window(0, 0, anchor="nw", window=self)
        self._region_job = None
        self.controller = ScrollController(self._outer, self.canvas)
        self.canvas.configure(yscrollcommand=lambda a, b: self.scrollbar.set(float(a), float(b)))
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.bind("<Configure>", self._on_inner_configure, "+")
        register(self._outer, self.controller, self._overflow, scale)

    # -- розміщення: віджет у розкладці — це контейнер
    def pack(self, *args, **kwargs):
        return self._outer.pack(*args, **kwargs)

    def grid(self, *args, **kwargs):
        return self._outer.grid(*args, **kwargs)

    def place(self, *args, **kwargs):
        return self._outer.place(*args, **kwargs)

    def pack_forget(self):
        return self._outer.pack_forget()

    def grid_forget(self):
        return self._outer.grid_forget()

    def grid_remove(self):
        return self._outer.grid_remove()

    def place_forget(self):
        return self._outer.place_forget()

    def destroy(self):
        tk.Frame.destroy(self)
        if not self._outer.dying:  # прямий виклик: разом із ним іде й контейнер
            try:
                self._outer.destroy()
            except tk.TclError:
                pass

    # -- прокрутка
    def _on_canvas_configure(self, event) -> None:
        if self._fill_width:
            self.canvas.itemconfigure(self._window, width=event.width)
        self._update_region()

    def _on_inner_configure(self, _event) -> None:
        if self._region_job is None:
            self._region_job = self.after_idle(self._update_region)

    def _update_region(self) -> None:
        self._region_job = None
        try:
            width = self.canvas.winfo_width()
            height = max(self.winfo_reqheight(), 1)
            self.canvas.configure(scrollregion=(0, 0, width, height))
            if height <= self.canvas.winfo_height():
                self.canvas.yview_moveto(0)
        except tk.TclError:
            pass

    def _overflow(self) -> bool:
        return self.winfo_reqheight() > self.canvas.winfo_height() > 1

    def _on_scrollbar(self, kind: str, value, *_rest) -> None:
        if kind == "moveto":
            self.controller.moveto(float(value))
        else:
            self.controller.by_pixels(int(value) * 3 * 48)

    def scroll_to_top(self) -> None:
        self.controller.moveto_now(0.0)

    def scroll_to_bottom(self) -> None:
        self.controller.moveto_now(1.0)
