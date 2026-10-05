"""Модальні вікна ВСЕРЕДИНІ головного вікна Lagnix (замість окремих Toplevel і messagebox).

Поверх усього вмісту накладається напівпрозоре затемнення (знімок вікна, притемнений
Pillow, з тінню під карткою), по центру — картка в стилі програми: заголовок, опис,
прокручуваний вміст і кнопки внизу, закріплені. Поява/зникнення ~150 мс.

  * одночасно відкрите лише одне вікно: друге, відкрите поки є перше, не показується
    (блокуючий виклик одразу повертає «скасовано»); вікно, що вже закривається
    (згасає), не заважає — наступне замінює його миттєво;
  * клік по затемненню і Esc закривають вікно (крім випадків, де йде дія —
    `dismissable = False`);
  * ширина ~560–720 px, висота до 80% вікна, зайве прокручується, тож вміст не обрізається.

Блокуючі помічники (як messagebox): confirm(), choose(), notify(). Для складного
вмісту — клас Modal: `modal.body` (батько вмісту), `add_button()`, `show()` (без очікування)
або `run()` (чекає закриття й повертає значення натиснутої кнопки).

Системні вікна Windows лишаються лише для UAC і вибору файлів/папок.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFilter, ImageGrab, ImageTk

from core.i18n import t
from ui import theme
from ui.widgets.scroll import ScrollFrame, widget_scale

MIN_WIDTH_DP = 560
MAX_WIDTH_DP = 720
WIDTH_FRACTION = 0.62
MAX_HEIGHT_FRACTION = 0.8
EDGE_MARGIN_DP = 24
PAD_DP = 22
RADIUS_DP = 16
DIM_AMOUNT = 0.62          # наскільки затемнюється вміст вікна
DIM_COLOR = (5, 8, 15)
FADE_MS = 150
FADE_FRAME_MS = 16
SHADOW_BLUR_DP = 22
SHADOW_OFFSET_DP = 10
SHADOW_ALPHA = 150

_KIND_COLORS = {"warning": theme.WARNING, "error": theme.ERROR, "danger": theme.ERROR, "info": theme.ACCENT_BLUE}

_BUTTON_STYLES = {
    "primary": {"fg_color": theme.ACCENT_BLUE_DIM},
    "danger": {"fg_color": "#a8283f", "hover_color": theme.ERROR, "text_color": "#ffffff"},
    "secondary": {"fg_color": "transparent", "border_width": 1, "border_color": theme.BORDER,
                  "hover_color": theme.BG_PANEL_LIGHT, "text_color": theme.TEXT_MAIN},
    "ghost": {"fg_color": "transparent", "hover_color": theme.BG_PANEL_LIGHT, "text_color": theme.TEXT_DIM},
}

_escape_bound = False


def _on_escape(_event=None):
    modal = Modal.current
    if modal is not None and modal.dismissable and not modal.closed:
        modal.close(modal.cancel_value)
        return "break"
    return None


class Modal:
    current: "Modal | None" = None   # показане (не закрите) вікно
    _fading: "Modal | None" = None   # закрите вікно, що ще згасає

    def __init__(self, master, title: str, description: str = "", *, warning: str = "", kind: str | None = None,
                 dismissable: bool = True, cancel_value=None, scroll: bool = True,
                 font_family: str | None = None, min_width_dp: int = MIN_WIDTH_DP):
        global _escape_bound
        self.root = master.winfo_toplevel()
        self.dismissable = dismissable
        self.cancel_value = cancel_value
        self.result = cancel_value
        self.closed = False
        self.shown = False
        self.on_close: list = []
        self._scroll_mode = scroll
        self._buttons: list[tuple[ctk.CTkButton, bool]] = []   # (кнопка, ліворуч)
        self._var = tk.IntVar(self.root, 0)
        self._photo = None
        self._job = None
        self._base: Image.Image | None = None
        self._dim: Image.Image | None = None

        getter = getattr(self.root, "_get_window_scaling", None)
        self._scale = getter() if getter else widget_scale(self.root)
        root_w_dp = max(self.root.winfo_width(), 1) / self._scale
        wanted = max(min_width_dp, min(MAX_WIDTH_DP, root_w_dp * WIDTH_FRACTION))
        self.width_dp = round(max(240, min(wanted, root_w_dp - 2 * EDGE_MARGIN_DP)))
        # ширина тексту всередині картки (без полів і повзунка)
        self.wrap_dp = round(self.width_dp - 2 * PAD_DP - 14)

        family = font_family or theme.font_family()
        self._title_font = ctk.CTkFont(family=family, size=17, weight="bold")
        self._body_font = ctk.CTkFont(family=family, size=13)
        self._small_font = ctk.CTkFont(family=family, size=12)
        self._button_font = ctk.CTkFont(family=family, size=13)

        self.overlay = tk.Frame(self.root, bd=0, highlightthickness=0, bg=_hex(DIM_COLOR), takefocus=1)
        self._backdrop = tk.Label(self.overlay, bd=0, highlightthickness=0, bg=_hex(DIM_COLOR), anchor="nw")
        self._backdrop.place(x=0, y=0, relwidth=1, relheight=1)
        self.card = ctk.CTkFrame(self.overlay, corner_radius=RADIUS_DP, fg_color=theme.BG_PANEL,
                                 border_width=1, border_color=theme.BORDER, width=self.width_dp)

        accent = _KIND_COLORS.get(kind or "")
        if accent:
            tk.Frame(self.card, height=3, bg=accent, bd=0, highlightthickness=0).pack(
                fill="x", padx=RADIUS_DP + 6, pady=(10, 0))
        self.title_label = ctk.CTkLabel(self.card, text=title, font=self._title_font, anchor="w", justify="left",
                                        wraplength=self.wrap_dp)
        self.title_label.pack(fill="x", padx=PAD_DP, pady=(16, 8))

        self.footer = theme.plain_frame(self.card)
        self.footer.pack(side="bottom", fill="x", padx=PAD_DP, pady=(14, 18))

        if scroll:
            self._scroller = ScrollFrame(self.card, width=1, height=10, scrollbar_gap=0)
            self._scroller.pack(fill="x", padx=PAD_DP)
            content = self._scroller
        else:
            self._scroller = None
            content = theme.plain_frame(self.card)
            content.pack(fill="both", expand=True, padx=PAD_DP)
        self.content = content
        if description:
            self.description_label = ctk.CTkLabel(
                content, text=description, font=self._body_font, text_color=theme.TEXT_DIM, anchor="w",
                justify="left", wraplength=self.wrap_dp)
            self.description_label.pack(fill="x")
        if warning:
            ctk.CTkLabel(content, text=warning, font=self._small_font, text_color=theme.WARNING, anchor="w",
                         justify="left", wraplength=self.wrap_dp).pack(fill="x", pady=(8, 0))
        self.body = theme.plain_frame(content)
        self.body.pack(fill="both", expand=True, pady=(10, 0) if (description or warning) else (0, 0))

        if not _escape_bound:
            _escape_bound = True
            tk.Misc.bind_all(self.root, "<Escape>", _on_escape, "+")

    # ------------------------------------------------------------ кнопки

    def add_button(self, text: str, value=None, style: str = "secondary", command=None, left: bool = False,
                   enabled: bool = True) -> ctk.CTkButton:
        """Кнопка внизу. Без `command` — закриває вікно зі значенням `value`; з `command` — лише викликає її."""
        button = ctk.CTkButton(
            self.footer, text=text, width=100 if style in ("secondary", "ghost") else 10, height=32,
            corner_radius=8, font=self._button_font,
            command=command if command is not None else (lambda: self.close(value)),
            state="normal" if enabled else "disabled", **_BUTTON_STYLES[style],
        )
        self._buttons.append((button, left))
        return button

    def _pack_buttons(self) -> None:
        right = [b for b, left in self._buttons if not left]
        left = [b for b, is_left in self._buttons if is_left]
        for button in reversed(right):
            button.pack(side="right", padx=(8, 0))
        for button in left:
            button.pack(side="left", padx=(0, 8))

    # ------------------------------------------------------------ показ

    def show(self) -> bool:
        """Показує вікно (без очікування). False — не показано, бо вже відкрите інше."""
        if self.closed or self.shown:
            return self.shown
        if Modal.current is not None and not Modal.current.closed:
            self.closed = True
            self._destroy_widgets()
            return False
        self.shown = True
        Modal.current = self
        self._pack_buttons()
        reuse = Modal._fading
        base = reuse._base if reuse is not None and reuse._base is not None else None
        if reuse is not None:
            reuse._destroy_widgets()
        self._layout_and_compose(base)
        self._backdrop.configure(image=self._photo)
        self.overlay.place(x=0, y=0, relwidth=1, relheight=1)
        self.overlay.lift()
        self.overlay.bind("<Button-1>", self._on_dim_click)
        self._backdrop.bind("<Button-1>", self._on_dim_click)
        try:
            self.overlay.grab_set()
        except tk.TclError:
            pass
        self.overlay.focus_set()
        self._animate(0.0, 1.0, self._reveal_card_at)
        return True

    def run(self):
        """Показує вікно й чекає закриття (як messagebox): -> значення кнопки або cancel_value."""
        if not self.show():
            return self.cancel_value
        try:
            self.root.wait_variable(self._var)
        except tk.TclError:
            pass
        return self.result

    def close(self, value=None) -> None:
        if self.closed:
            return
        self.closed = True
        self.result = value
        if Modal.current is self:
            Modal.current = None
        for callback in list(self.on_close):
            try:
                callback(value)
            except Exception:
                pass
        try:
            self.overlay.grab_release()
        except tk.TclError:
            pass
        if not self.shown:
            self._destroy_widgets()
        else:
            try:
                self.card.place_forget()
                Modal._fading = self
                self._animate(1.0, 0.0, None, on_end=self._finish_fade)
            except tk.TclError:
                pass
        try:
            self._var.set(1)
        except tk.TclError:
            pass

    def _finish_fade(self) -> None:
        if Modal._fading is self:
            Modal._fading = None
        self._destroy_widgets()

    def _destroy_widgets(self) -> None:
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if Modal._fading is self:
            Modal._fading = None
        try:
            self.overlay.destroy()
        except tk.TclError:
            pass

    def _on_dim_click(self, _event=None) -> None:
        if self.dismissable and not self.closed:
            self.close(self.cancel_value)

    # ------------------------------------------------------ розмір і фон

    def _layout_and_compose(self, base: Image.Image | None) -> None:
        root = self.root
        S = self._scale
        root.update_idletasks()
        rw, rh = max(root.winfo_width(), 2), max(root.winfo_height(), 2)

        if self._scroller is not None:
            self._scroller._outer.configure(width=round((self.width_dp - 2 * PAD_DP) * S))
            self.card.update_idletasks()
            chrome = self.card.winfo_reqheight() - self._scroller._outer.winfo_reqheight()
            limit = max(round(rh * MAX_HEIGHT_FRACTION) - chrome, 80)
            self._scroller._outer.configure(height=max(min(self._scroller.winfo_reqheight(), limit), 1))
        self.card.update_idletasks()
        card_w = round(self.width_dp * S)
        card_h = min(self.card.winfo_reqheight(), rh - 8)
        self.card.configure(width=self.width_dp, height=card_h / S)
        self.card.pack_propagate(False)  # розмір картки задано явно (CTk не дозволяє width/height у place)
        self._card_geometry = (card_w, card_h)

        if base is None or base.size != (rw, rh):
            base = self._capture(rw, rh)
        self._base = base
        self._dim = self._compose_dim(base, rw, rh, card_w, card_h)
        self._photo = ImageTk.PhotoImage(base)
        # кути картки зливаються з притемненим фоном довкола неї
        x0, y0 = (rw - card_w) // 2, (rh - card_h) // 2
        region = self._dim.crop((max(x0, 0), max(y0, 0), min(x0 + card_w, rw), min(y0 + card_h, rh)))
        avg = region.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
        self.card.configure(bg_color=_hex(avg))

    def _capture(self, rw: int, rh: int) -> Image.Image:
        """Знімок вмісту головного вікна; якщо не вийшло (згорнуте тощо) — суцільний фон."""
        root = self.root
        try:
            if root.winfo_viewable() and root.state() == "normal":
                x, y = root.winfo_rootx(), root.winfo_rooty()
                return ImageGrab.grab(bbox=(x, y, x + rw, y + rh), all_screens=True).convert("RGB")
        except Exception:
            pass
        return Image.new("RGB", (rw, rh), _rgb(theme.BG_MAIN))

    def _compose_dim(self, base: Image.Image, rw: int, rh: int, card_w: int, card_h: int) -> Image.Image:
        dim = Image.blend(base, Image.new("RGB", base.size, DIM_COLOR), DIM_AMOUNT).convert("RGBA")
        S = self._scale
        blur, offset = round(SHADOW_BLUR_DP * S), round(SHADOW_OFFSET_DP * S)
        x0, y0 = (rw - card_w) // 2, (rh - card_h) // 2
        shadow = Image.new("L", dim.size, 0)
        ImageDraw.Draw(shadow).rounded_rectangle(
            (x0, y0 + offset, x0 + card_w, y0 + card_h + offset), radius=round(RADIUS_DP * S), fill=SHADOW_ALPHA)
        shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
        dim.paste(Image.new("RGBA", dim.size, (0, 0, 0, 255)), (0, 0), shadow)
        return dim.convert("RGB")

    # ---------------------------------------------------------- анімація

    def _animate(self, start: float, end: float, on_progress, on_end=None) -> None:
        """Плавна зміна затемнення start->end за FADE_MS; на_progress(поточне) — для появи картки."""
        steps = max(round(FADE_MS / FADE_FRAME_MS), 1) if theme.animations_enabled() else 1
        state = {"i": 0}

        def tick():
            self._job = None
            state["i"] += 1
            k = state["i"] / steps
            level = start + (end - start) * theme.ease_out_cubic(min(k, 1.0))
            try:
                if state["i"] >= steps:
                    level = end
                self._photo = ImageTk.PhotoImage(Image.blend(self._base, self._dim, level))
                self._backdrop.configure(image=self._photo)
                if on_progress is not None:
                    on_progress(level)
                if state["i"] < steps:
                    self._job = self.root.after(FADE_FRAME_MS, tick)
                elif on_end is not None:
                    on_end()
            except tk.TclError:
                pass

        tick()

    def _reveal_card_at(self, level: float) -> None:
        if level >= 0.5 and not self.card.winfo_ismapped() and not self.closed:
            self.card.place(relx=0.5, rely=0.5, anchor="center")


def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb[:3])


# ------------------------------------------------------- блокуючі помічники

def confirm(master, title: str, message: str, confirm_text: str | None = None, danger: bool = False,
            cancel_text: str | None = None, kind: str | None = None) -> bool:
    """Підтвердження (як messagebox.askyesno): True — натиснуто підтвердження."""
    modal = Modal(master, title, message, kind="danger" if danger and kind is None else kind, cancel_value=False)
    modal.add_button(cancel_text or (t("common.cancel") if confirm_text else t("common.no")), False, "secondary")
    modal.add_button(confirm_text or t("common.yes"), True, "danger" if danger else "primary")
    return bool(modal.run())


def choose(master, title: str, message: str, yes_text: str | None = None, no_text: str | None = None) -> bool | None:
    """Так / Ні / Скасувати (як messagebox.askyesnocancel): True / False / None."""
    modal = Modal(master, title, message, cancel_value=None)
    modal.add_button(t("common.cancel"), None, "ghost")
    modal.add_button(no_text or t("common.no"), False, "secondary")
    modal.add_button(yes_text or t("common.yes"), True, "primary")
    return modal.run()


def notify(master, title: str, message: str, kind: str = "info") -> None:
    """Повідомлення з однією кнопкою (як messagebox.showinfo/showerror/showwarning)."""
    modal = Modal(master, title, message, kind=kind)
    modal.add_button(t("common.ok"), None, "primary")
    modal.run()
