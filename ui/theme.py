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


def font_title() -> ctk.CTkFont:
    return ctk.CTkFont(family=_FONT_FAMILY, size=22, weight="bold")


def font_header() -> ctk.CTkFont:
    return ctk.CTkFont(family=_FONT_FAMILY, size=15, weight="bold")


def font_body() -> ctk.CTkFont:
    return ctk.CTkFont(family=_FONT_FAMILY, size=13)


def font_small() -> ctk.CTkFont:
    return ctk.CTkFont(family=_FONT_FAMILY, size=11)


# --------------------------------------------------------------- анімація

def ease_out_cubic(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


class _ColorAnimator:
    """Плавно перетворює колір canvas-фігур/лейблів кнопки чи рамку картки
    з поточного значення на цільове за duration секунд, ~60 кадрів/с.

    Використовує after() віджета-власника; попередня анімація на тому
    самому об'єкті скасовується, а нова стартує з кольору, показаного
    просто зараз — тому швидке "туди-сюди" наведення не смикається.
    """

    def __init__(self, owner: tk.Widget, apply_fn):
        self._owner = owner
        self._apply = apply_fn
        self._job = None
        self._current: str | None = None

    def _rgb(self, color: str) -> tuple[int, int, int]:
        r, g, b = self._owner.winfo_rgb(color)
        return r // 256, g // 256, b // 256

    def _lerp(self, c1: str, c2: str, t: float) -> str:
        r1, g1, b1 = self._rgb(c1)
        r2, g2, b2 = self._rgb(c2)
        r = round(r1 + (r2 - r1) * t)
        g = round(g1 + (g2 - g1) * t)
        b = round(b1 + (b2 - b1) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    def set_immediate(self, color: str) -> None:
        self.cancel()
        self._current = color
        self._apply(color)

    def cancel(self) -> None:
        if self._job is not None:
            try:
                self._owner.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def animate_to(self, target: str, duration: float = 0.15) -> None:
        if not self._owner.winfo_exists():
            return
        if self._current is None or not animations_enabled():
            self.set_immediate(target)
            return
        if self._current == target:
            return
        self.cancel()
        start_color = self._current
        start_time = time.perf_counter()

        def step():
            if not self._owner.winfo_exists():
                self._job = None
                return
            t = (time.perf_counter() - start_time) / duration
            if t >= 1.0:
                self._current = target
                self._apply(target)
                self._job = None
                return
            color = self._lerp(start_color, target, ease_out_cubic(t))
            self._current = color
            self._apply(color)
            self._job = self._owner.after(12, step)

        step()


class ValueAnimator:
    """Як _ColorAnimator, але для довільного числового значення (позиція,
    прозорість-заміна тощо). Використовується, наприклад, для індикатора
    активної вкладки в бічному меню й переходів між вкладками."""

    def __init__(self, owner: tk.Widget, apply_fn):
        self._owner = owner
        self._apply = apply_fn
        self._job = None
        self.current: float | None = None

    def set_immediate(self, value: float) -> None:
        self.cancel()
        self.current = value
        self._apply(value)

    def cancel(self) -> None:
        if self._job is not None:
            try:
                self._owner.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def animate_to(self, target: float, duration: float = 0.2, on_done=None) -> None:
        if not self._owner.winfo_exists():
            return
        if self.current is None or not animations_enabled():
            self.set_immediate(target)
            if on_done:
                on_done()
            return
        self.cancel()
        start_value = self.current
        start_time = time.perf_counter()

        def step():
            if not self._owner.winfo_exists():
                self._job = None
                return
            t = (time.perf_counter() - start_time) / duration
            if t >= 1.0:
                self.current = target
                self._apply(target)
                self._job = None
                if on_done:
                    on_done()
                return
            self.current = start_value + (target - start_value) * ease_out_cubic(t)
            self._apply(self.current)
            self._job = self._owner.after(12, step)

        step()


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


# --------------------------------------------------------- AnimatedButton

class AnimatedButton(ctk.CTkButton):
    """CTkButton із плавним переходом кольору при наведенні/клацанні.

    Поведінково ідентична стандартній CTkButton (той самий конструктор,
    ті самі публічні методи) — лише _on_enter/_on_leave тепер анімують
    колір замість миттєвої зміни. Клікова "просадка" вже була вбудована
    в CTkButton (_on_release -> _on_leave, потім через 100 мс назад до
    hover-кольору) і автоматично стає плавною разом з рештою.
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
        self._mouse_inside = True
        if self._hover is True and self._state == "normal":
            target = self._fg_color if self._hover_color is None else self._hover_color
            self._color_anim.animate_to(self._apply_appearance_mode(target))
            if self.play_hover_sound:
                _play_hover_tick()

    def _on_leave(self, event=None):
        self._mouse_inside = False
        self._click_animation_running = False
        target = self._bg_color if self._fg_color == "transparent" else self._fg_color
        self._color_anim.animate_to(self._apply_appearance_mode(target))

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
        self._canvas.bind("<Enter>", self._on_card_enter, add="+")
        self._canvas.bind("<Leave>", self._on_card_leave, add="+")

    def _apply_border_color(self, color: str) -> None:
        try:
            self.configure(border_color=color)
        except tk.TclError:
            pass

    def _on_card_enter(self, _event=None) -> None:
        self._border_anim.animate_to(ACCENT_BLUE, duration=0.18)

    def _on_card_leave(self, _event=None) -> None:
        self._border_anim.animate_to(self._apply_appearance_mode(self._rest_border), duration=0.18)


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
