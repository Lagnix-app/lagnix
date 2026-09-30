"""Єдина палітра, шрифти та базові анімовані віджети PulseFPS.

Кольори й відступи звідси — джерело істини для всього інтерфейсу; більшість
стандартних CTk-віджетів (CTkFrame/CTkButton/CTkCheckBox тощо) додатково
беруть кольори з `assets/pulsefps_theme.json` (застосовується один раз у
main.py через `ctk.set_default_color_theme`), тож модулі вкладок можуть і
надалі створювати `ctk.CTkButton(...)`/`ctk.CTkFrame(..., corner_radius=10)`
без явних кольорів і отримувати вигляд PulseFPS «безкоштовно».

AnimatedButton і AnimatedCard монкі-патчать `customtkinter.CTkButton` і
`customtkinter.CTkFrame` (див. кінець файлу) — це свідомий вибір: додати
плавне наведення/клік і підсвічування рамки карток одразу на всіх 25+
кнопках і картках проєкту без правки кожного файлу вкладки. Обидва класи
успадковують оригінальну поведінку 1:1 і лише замінюють миттєву зміну
кольору на анімовану, тож жоден виклик існуючого коду не ламається.
"""

from __future__ import annotations

import sys
import time
import tkinter as tk

import customtkinter as ctk

# ---------------------------------------------------------------- палітра

BG_MAIN = "#0d1321"
BG_PANEL = "#151c2c"
BG_PANEL_LIGHT = "#1b2436"

ACCENT_GREEN = "#2ee59d"
ACCENT_GREEN_DIM = "#28c98a"
ACCENT_BLUE = "#4fc3ff"
ACCENT_BLUE_DIM = "#1f7ae0"
ERROR = "#ff5c7a"
WARNING = "#e0a52f"

TEXT_MAIN = "#eef2f8"
TEXT_DIM = "#8b95ab"
BORDER = "#232d42"

CORNER_RADIUS = 12
PAD_S = 8
PAD_M = 16
PAD_L = 20

_FONT_FAMILY = "Segoe UI"


# ----------------------------------------------------- перемикачі анімацій
# Два незалежні глобальні флаги (вкладка «Налаштування», розділ
# «Інтерфейс»): _animations_enabled вимикає плавні переходи кольору/позиції
# нижче (наведення, клік, слайд вкладок) — для слабких ПК; _robot_animation
# читають ui/widgets/cleaner_bot.py та ui/widgets/logo_widget.py окремо,
# бо там власні after()-цикли, а не _ColorAnimator/ValueAnimator.

_animations_enabled = True
_robot_animation_enabled = True


def set_animations_enabled(enabled: bool) -> None:
    global _animations_enabled
    _animations_enabled = bool(enabled)


def animations_enabled() -> bool:
    return _animations_enabled


def set_robot_animation_enabled(enabled: bool) -> None:
    global _robot_animation_enabled
    _robot_animation_enabled = bool(enabled)


def robot_animation_enabled() -> bool:
    return _robot_animation_enabled


def _bg_of(widget) -> str:
    """Суцільний колір фону, який видно під віджетом (для прозорих CTk-рамок)."""
    while widget is not None:
        if hasattr(widget, "_apply_appearance_mode"):
            try:
                fg = widget.cget("fg_color")
            except (tk.TclError, ValueError):
                fg = "transparent"
            if fg != "transparent":
                return widget._apply_appearance_mode(fg)
        else:
            try:
                return widget.cget("bg")
            except tk.TclError:
                pass
        widget = getattr(widget, "master", None)
    return BG_MAIN


def plain_frame(parent, **options) -> tk.Frame:
    """Проста невидима рамка-розкладка: один tk.Frame замість CTkFrame (той — це
    рамка + canvas, два вікна Windows, які треба переміщувати при кожній
    прокрутці). Фон суцільний, як у батька; висота/ширина — у пікселях."""
    return tk.Frame(parent, bg=_bg_of(parent), bd=0, highlightthickness=0, **options)


def set_text(label, text: str, **options) -> None:
    """label.configure(text=...), лише якщо текст чи опції змінилися: CTkLabel
    перемальовує себе на КОЖЕН configure, навіть з тим самим текстом, а
    фонові оновлення (Монітор, Мережа) шлють однакові значення щосекунди."""
    key = (text, tuple(sorted(options.items())))
    if getattr(label, "_pulse_text_key", None) == key:
        return
    label._pulse_text_key = key
    label.configure(text=text, **options)


def font_title() -> ctk.CTkFont:
    return ctk.CTkFont(family=_FONT_FAMILY, size=22, weight="bold")


def font_header() -> ctk.CTkFont:
    return ctk.CTkFont(family=_FONT_FAMILY, size=15, weight="bold")


def font_body() -> ctk.CTkFont:
    return ctk.CTkFont(family=_FONT_FAMILY, size=13)


def font_small() -> ctk.CTkFont:
    return ctk.CTkFont(family=_FONT_FAMILY, size=11)


# --------------------------------------------------------------- анімація
# Продуктивність: усі анімації (колір наведення, рамки карток, слайд вкладок,
# кільця) крутить ОДИН спільний таймер _Ticker, а не власний after()-ланцюжок
# кожного віджета — за кадр виконується один колбек, скільки б елементів не
# анімувалося. На елемент — не більше однієї анімації (нова замінює стару).
# Під час прокрутки й при швидкому русі курсора колір змінюється миттєво:
# анімувати те, що за 50 мс зникне з-під курсора, — лише витрачати кадри.

_FRAME_MS = 15
_SCROLL_QUIET_S = 0.15  # стільки після останньої прокрутки анімації й живі оновлення вимкнені
_FAST_POINTER_S = 0.07  # Enter/Leave частіше за це — курсор "пролітає"

_last_scroll_at = 0.0
_last_crossing_at = 0.0


def notify_scroll() -> None:
    """Викликається будь-якою прокруткою: на короткий час вимикає анімації."""
    global _last_scroll_at
    _last_scroll_at = time.perf_counter()


def is_scrolling() -> bool:
    return time.perf_counter() - _last_scroll_at < _SCROLL_QUIET_S


def _pointer_crossing_is_fast() -> bool:
    """Реєструє перетин межі елемента курсором; True, якщо попередній був
    щойно — тобто курсор швидко пролітає над кількома елементами."""
    global _last_crossing_at
    now = time.perf_counter()
    fast = now - _last_crossing_at < _FAST_POINTER_S
    _last_crossing_at = now
    return fast


def _instant() -> bool:
    return not _animations_enabled or is_scrolling()


def pointer_inside(widget: tk.Misc) -> bool:
    """Чи курсор усе ще в межах віджета (Leave буває й при переході на
    дочірній віджет — тоді стан наведення скидати не треба)."""
    try:
        if not widget.winfo_ismapped():
            return False
        px, py = widget.winfo_pointerxy()
        x, y = widget.winfo_rootx(), widget.winfo_rooty()
        return x <= px < x + widget.winfo_width() and y <= py < y + widget.winfo_height()
    except tk.TclError:
        return False


def ease_out_cubic(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


_rgb_cache: dict[str, tuple[int, int, int]] = {}


def _rgb(owner: tk.Misc, color: str) -> tuple[int, int, int]:
    value = _rgb_cache.get(color)
    if value is None:
        if color.startswith("#") and len(color) == 7:
            value = (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))
        else:  # іменовані кольори ("gray" тощо) — один раз через Tk
            r, g, b = owner.winfo_rgb(color)
            value = (r // 256, g // 256, b // 256)
        _rgb_cache[color] = value
    return value


def lerp_color(owner: tk.Misc, c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = _rgb(owner, c1)
    r2, g2, b2 = _rgb(owner, c2)
    return f"#{round(r1 + (r2 - r1) * t):02x}{round(g1 + (g2 - g1) * t):02x}{round(b1 + (b2 - b1) * t):02x}"


class _Ticker:
    """Єдиний after()-цикл для всіх активних анімацій."""

    def __init__(self):
        self._active: dict = {}  # animator -> None (впорядкована множина)
        self._job = None
        self._root = None

    def add(self, animator, owner: tk.Misc) -> None:
        self._active[animator] = None
        if self._job is None:
            try:
                self._root = owner._root()
                self._job = self._root.after(_FRAME_MS, self._tick)
            except (tk.TclError, RuntimeError):
                self._active.pop(animator, None)

    def remove(self, animator) -> None:
        self._active.pop(animator, None)

    def _tick(self) -> None:
        self._job = None
        now = time.perf_counter()
        for animator in list(self._active):
            try:
                alive = animator._step(now)
            except tk.TclError:  # віджет знищено посеред анімації
                alive = False
            if not alive:
                self._active.pop(animator, None)
        if self._active:
            try:
                self._job = self._root.after(_FRAME_MS, self._tick)
            except (tk.TclError, RuntimeError):
                self._active.clear()


_ticker = _Ticker()
ticker = _ticker
"""Спільний таймер анімацій: ticker.add(obj, widget) викликає obj._step(now)
щокадру, доки той повертає True (використовують і списки на Canvas)."""


class _ColorAnimator:
    """Плавно перетворює колір з поточного значення на цільове за duration
    секунд. Нова анімація замінює попередню й стартує з кольору, показаного
    просто зараз, — тому швидке "туди-сюди" наведення не смикається."""

    def __init__(self, owner: tk.Widget, apply_fn):
        self._owner = owner
        self._apply = apply_fn
        self._current: str | None = None
        self._start = self._target = None
        self._t0 = 0.0
        self._duration = 0.15

    def set_immediate(self, color: str) -> None:
        self.cancel()
        self._current = color
        self._apply(color)

    def cancel(self) -> None:
        _ticker.remove(self)

    def animate_to(self, target: str, duration: float = 0.15, instant: bool = False) -> None:
        if self._current is None or instant or _instant():
            self.set_immediate(target)
            return
        if self._current == target:
            self.cancel()
            return
        self._start, self._target = self._current, target
        self._t0 = time.perf_counter()
        self._duration = duration
        _ticker.add(self, self._owner)

    def _step(self, now: float) -> bool:
        t = (now - self._t0) / self._duration
        if t >= 1.0:
            self._current = self._target
            self._apply(self._target)
            return False
        self._current = lerp_color(self._owner, self._start, self._target, ease_out_cubic(t))
        self._apply(self._current)
        return True


class ValueAnimator:
    """Як _ColorAnimator, але для довільного числового значення (позиція
    індикатора активної вкладки, слайд вкладок, відсоток кільця)."""

    def __init__(self, owner: tk.Widget, apply_fn):
        self._owner = owner
        self._apply = apply_fn
        self.current: float | None = None
        self._start = self._target = 0.0
        self._t0 = 0.0
        self._duration = 0.2
        self._on_done = None

    def set_immediate(self, value: float) -> None:
        self.cancel()
        self.current = value
        self._apply(value)

    def cancel(self) -> None:
        _ticker.remove(self)

    def animate_to(self, target: float, duration: float = 0.2, on_done=None) -> None:
        if self.current is None or not _animations_enabled:
            self.set_immediate(target)
            if on_done:
                on_done()
            return
        self._start, self._target = self.current, target
        self._t0 = time.perf_counter()
        self._duration = duration
        self._on_done = on_done
        _ticker.add(self, self._owner)

    def _step(self, now: float) -> bool:
        t = (now - self._t0) / self._duration
        if t >= 1.0:
            self.current = self._target
            self._apply(self._target)
            if self._on_done:
                self._on_done()
            return False
        self.current = self._start + (self._target - self._start) * ease_out_cubic(t)
        self._apply(self.current)
        return True


def _play_hover_tick() -> None:
    try:
        from core import sounds
        sounds.play_hover()
    except Exception:
        pass


def _play_click() -> None:
    try:
        from core import sounds
        sounds.play_click()
    except Exception:
        pass


play_click = _play_click


# --------------------------------------------------------- AnimatedButton

class AnimatedButton(ctk.CTkButton):
    """CTkButton із плавним переходом кольору при наведенні/клацанні.

    Поведінково ідентична стандартній CTkButton (той самий конструктор,
    ті самі публічні методи) — лише _on_enter/_on_leave тепер анімують
    колір замість миттєвої зміни. Клікова "просадка" вже була вбудована
    в CTkButton (_on_release -> _on_leave, потім через 100 мс назад до
    hover-кольору) і автоматично стає плавною разом з рештою.

    Leave при переході курсора з canvas кнопки на її ж текст ігнорується
    (курсор лишився в межах кнопки) — інакше колір і звук "блимали" б.
    """

    play_hover_sound = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._color_anim = _ColorAnimator(self, self._apply_inner_color)
        base = self._bg_color if self._fg_color == "transparent" else self._fg_color
        try:
            self._color_anim.set_immediate(self._apply_appearance_mode(base))
        except tk.TclError:
            pass

    def _apply_inner_color(self, color: str) -> None:
        try:
            self._canvas.itemconfig("inner_parts", outline=color, fill=color)
            if self._text_label is not None:
                self._text_label.configure(bg=color)
            if self._image_label is not None:
                self._image_label.configure(bg=color)
        except tk.TclError:
            pass

    def _on_enter(self, event=None):
        if self._mouse_inside and event is not None:
            return  # перехід між canvas і текстом тієї самої кнопки
        self._mouse_inside = True
        if self._hover is True and self._state == "normal":
            target = self._fg_color if self._hover_color is None else self._hover_color
            self._color_anim.animate_to(
                self._apply_appearance_mode(target), instant=_pointer_crossing_is_fast(),
            )
            if self.play_hover_sound:
                _play_hover_tick()

    def _on_leave(self, event=None):
        if event is not None and pointer_inside(self):
            return
        self._mouse_inside = False
        self._click_animation_running = False
        target = self._bg_color if self._fg_color == "transparent" else self._fg_color
        self._color_anim.animate_to(
            self._apply_appearance_mode(target), instant=_pointer_crossing_is_fast(),
        )

    def _on_release(self, event=None):
        if self._mouse_inside and self._state != tk.DISABLED:
            _play_click()
        super()._on_release(event)


class NavButton(AnimatedButton):
    """Пункт бічного меню: як AnimatedButton, але з тихим "тіком" при наведенні."""

    play_hover_sound = True


# ----------------------------------------------------------- AnimatedCard

class AnimatedCard(ctk.CTkFrame):
    """CTkFrame із ледь помітним підсвіченням рамки при наведенні.

    Анімація вмикається лише для "карток" — непрозорих (fg_color не
    "transparent") панелей із заокругленням, як самостійно, так і через
    глобальний монкі-патч CTkFrame нижче. Суто розкладкові/прозорі
    контейнери (переважна більшість фреймів у проєкті) не чіпаються.

    Продуктивність: Enter/Leave слухаємо на самому tk.Frame (Tk надсилає їх,
    лише коли курсор входить у картку чи її нащадків ззовні або покидає їх
    усі — переходи між дочірніми віджетами подій не дають), а колір рамки
    міняємо прямо в canvas (itemconfig "border_parts"), без configure(), який
    щоразу перебудовував би всю заокруглену фігуру.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._card_hover_enabled = self._fg_color != "transparent" and self._corner_radius > 0
        if not self._card_hover_enabled:
            return
        if self._border_width == 0:
            self.configure(border_width=1)
        self._rest_border = self._border_color
        self._border_anim = _ColorAnimator(self, self._apply_border_color)
        try:
            self._border_anim.set_immediate(self._apply_appearance_mode(self._rest_border))
        except tk.TclError:
            pass
        tk.Misc.bind(self, "<Enter>", self._on_card_enter, "+")
        tk.Misc.bind(self, "<Leave>", self._on_card_leave, "+")

    def _apply_border_color(self, color: str) -> None:
        try:
            self._canvas.itemconfig("border_parts", fill=color, outline=color)
        except tk.TclError:
            pass

    def _draw(self, no_color_updates=False):
        super()._draw(no_color_updates)
        # перебудова фігури (зміна розміру) скидає рамку до кольору спокою
        anim = getattr(self, "_border_anim", None)
        if anim is not None and anim._current is not None:
            self._apply_border_color(anim._current)

    def _on_card_enter(self, _event=None) -> None:
        self._border_anim.animate_to(ACCENT_BLUE, duration=0.18, instant=_pointer_crossing_is_fast())

    def _on_card_leave(self, _event=None) -> None:
        if pointer_inside(self):
            return
        self._border_anim.animate_to(
            self._apply_appearance_mode(self._rest_border), duration=0.18,
            instant=_pointer_crossing_is_fast(),
        )


# ----------------------------------------------- швидкий скролбар і прокрутка

def _scrollbar_set(self, start_value: float, end_value: float) -> None:
    """Заміна CTkScrollbar.set: оригінал на КОЖНУ прокрутку перемальовує
    фігуру й викликає update_idletasks() — синхронну перебудову всього вікна
    посеред обробки колеса (~9 мс). Тут перемальовка відкладається в
    after_idle і виконується раз за кадр, без update_idletasks()."""
    start_value, end_value = float(start_value), float(end_value)
    if (start_value, end_value) == (self._start_value, self._end_value):
        return
    self._start_value, self._end_value = start_value, end_value
    if getattr(self, "_fast_redraw_job", None) is None:
        try:
            self._fast_redraw_job = self.after_idle(self._fast_redraw)
        except tk.TclError:
            pass


def _scrollbar_fast_redraw(self) -> None:
    self._fast_redraw_job = None
    try:
        start, end = self._get_scrollbar_values_for_minimum_pixel_size()
        self._draw_engine.draw_rounded_scrollbar(
            self._apply_widget_scaling(self._current_width),
            self._apply_widget_scaling(self._current_height),
            self._apply_widget_scaling(self._corner_radius),
            self._apply_widget_scaling(self._border_spacing),
            start, end, self._orientation,
        )
        color = self._apply_appearance_mode(self._button_hover_color if self._hover_state else self._button_color)
        self._canvas.itemconfig("scrollbar_parts", fill=color, outline=color)
    except tk.TclError:
        pass


class _SmoothWheel:
    """Плавна прокрутка колесом для CTkScrollableFrame: замість стрибка на
    20 px за "клік" колеса вміст доїжджає до цілі за кілька кадрів."""

    def __init__(self, frame):
        self._frame = frame
        self._pending = 0.0  # px, ще не прокручені

    def add(self, pixels: float) -> None:
        self._pending += pixels
        if _animations_enabled:
            _ticker.add(self, self._frame)
        else:
            step = round(self._pending)
            self._pending = 0.0
            if step:
                self._frame._parent_canvas.yview_scroll(step, "units")

    def _step(self, _now: float) -> bool:
        step = self._pending * 0.35
        if abs(step) < 1:
            step = 0 if abs(self._pending) < 0.5 else (1 if self._pending > 0 else -1)
        step = round(step)
        if step == 0:
            self._pending = 0.0
            return False
        self._pending -= step
        self._frame._parent_canvas.yview_scroll(step, "units")
        notify_scroll()
        return True


_orig_mouse_wheel_all = ctk.CTkScrollableFrame._mouse_wheel_all


def _scrollable_mouse_wheel_all(self, event):
    if not self._check_if_valid_scroll(event.widget):
        return
    notify_scroll()
    if sys.platform.startswith("win") and not self._shift_pressed and self._orientation == "vertical":
        if self._parent_canvas.yview() != (0.0, 1.0):
            wheel = getattr(self, "_smooth_wheel", None)
            if wheel is None:
                wheel = self._smooth_wheel = _SmoothWheel(self)
            wheel.add(-event.delta / 6)
        return
    _orig_mouse_wheel_all(self, event)


# ------------------------------------------------------------- монкі-патч
# Виконується один раз при першому імпорті ui.theme (main_window.py
# імпортує його раніше за всі вкладки). Патчимо саме модуль customtkinter,
# бо вкладки викликають ctk.CTkButton(...)/ctk.CTkFrame(...) із префіксом
# модуля — атрибут резолвиться в момент виклику, тож патч діє і на вже
# написаний код вкладок без жодної правки їхніх файлів.

PlainFrame = ctk.CTkFrame
"""Оригінальний CTkFrame — для суто декоративних елементів (індикатор
активної вкладки тощо), де підсвічування рамки при наведенні не потрібне."""

if getattr(ctk, "CTkButton", None) is not AnimatedButton:
    ctk.CTkButton = AnimatedButton
if getattr(ctk, "CTkFrame", None) is not AnimatedCard:
    ctk.CTkFrame = AnimatedCard
if ctk.CTkScrollbar.set is not _scrollbar_set:
    ctk.CTkScrollbar.set = _scrollbar_set
    ctk.CTkScrollbar._fast_redraw = _scrollbar_fast_redraw
    ctk.CTkScrollableFrame._mouse_wheel_all = _scrollable_mouse_wheel_all
