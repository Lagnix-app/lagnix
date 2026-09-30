"""Вкладка «Твіки реєстру» — набір фіксованих твіків з core/tweaks.py.

Зверху — картки пресетів («Безпечний», «Збалансований», «Максимальний»,
«Повернути все як було»): кожна перед застосуванням показує список змін із
галочками. Нижче — блок «Режим Windows» і всі твіки по розділах з позначками
ризику й ефекту. Червоні («ризиковано») вмикаються лише вручну, з окремим
підтвердженням, і ніколи не потрапляють у пресети."""

import os
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from core import process_control
from core import tweaks as tweaks_core
from ui import bg, theme
from ui.widgets import confirm_dialog

_RISK_LABELS = {
    tweaks_core.RISK_SAFE: "безпечно",
    tweaks_core.RISK_CAUTION: "на свій розсуд",
    tweaks_core.RISK_DANGER: "ризиковано",
}
_RISK_COLORS = {
    tweaks_core.RISK_SAFE: theme.ACCENT_GREEN,
    tweaks_core.RISK_CAUTION: theme.WARNING,
    tweaks_core.RISK_DANGER: theme.ERROR,
}
_EFFECT_COLORS = {
    tweaks_core.EFFECT_NOTICEABLE: theme.ACCENT_BLUE,
    tweaks_core.EFFECT_SMALL: theme.TEXT_DIM,
    tweaks_core.EFFECT_DEPENDS: theme.TEXT_DIM,
}

# Тайм-аут фонових змін: перша зміна створює точку відновлення (до 90 с).
_APPLY_TIMEOUT_S = 240

_PRESETS = (
    (
        tweaks_core.PRESET_SAFE, "Безпечний",
        "Лише зелені твіки — без побічних ефектів.", theme.ACCENT_GREEN,
    ),
    (
        tweaks_core.PRESET_BALANCED, "Збалансований",
        "Зелені + жовті з невеликим, передбачуваним ефектом.", theme.ACCENT_BLUE,
    ),
    (
        tweaks_core.PRESET_MAX, "Максимальний",
        "Усі зелені й жовті. Частина жовтих діє лише на деяких ПК і має побічні ефекти.",
        theme.WARNING,
    ),
)

_MAX_WARNING = (
    "Максимальний пресет вмикає всі жовті твіки, зокрема ті, що допомагають лише на "
    "деяких ПК (MPO, алгоритм Нейгла, апаратне планування GPU, Power Throttling). "
    "Вони можуть нічого не дати або мати побічні ефекти — перегляньте список і "
    "зніміть галочки з того, що вам не потрібно. Червоні твіки сюди не входять."
)


def _chip(master, text: str, color: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        master, text=text, font=theme.font_small(), text_color=color,
        fg_color=theme.BG_PANEL_LIGHT, corner_radius=6, height=20,
    )


def _tweak_chips(master, tweak: tweaks_core.Tweak) -> list[ctk.CTkLabel]:
    chips = [
        _chip(master, f" ● {_RISK_LABELS[tweak.risk]} ", _RISK_COLORS[tweak.risk]),
        _chip(master, f" ефект: {tweaks_core.EFFECT_LABELS[tweak.effect]} ", _EFFECT_COLORS[tweak.effect]),
    ]
    if tweak.requires_reboot:
        chips.append(_chip(master, " потребує перезавантаження ", theme.TEXT_DIM))
    elif tweak.requires_logoff:
        chips.append(_chip(master, " потребує виходу з системи ", theme.TEXT_DIM))
    return chips


class TweakRow(ctk.CTkFrame):
    """Один твік: перемикач, назва, позначки ризику й ефекту, опис і примітки."""

    def __init__(self, master, tweak: tweaks_core.Tweak, on_toggle):
        super().__init__(master, fg_color="transparent")
        self.tweak = tweak
        self._on_toggle = on_toggle
        self._busy = False

        self.grid_columnconfigure(1, weight=1)

        self.switch_var = ctk.BooleanVar(value=False)
        self.switch = ctk.CTkSwitch(
            self, text="", variable=self.switch_var, width=40,
            command=lambda: self._on_toggle(self),
            **({"progress_color": theme.ERROR} if tweak.risk == tweaks_core.RISK_DANGER else {}),
        )
        self.switch.grid(row=0, column=0, padx=(0, 10), pady=8, sticky="n")

        text_frame = ctk.CTkFrame(self, fg_color="transparent")
        text_frame.grid(row=0, column=1, sticky="ew", pady=8)

        ctk.CTkLabel(
            text_frame, text=tweak.title, font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w", justify="left", wraplength=620,
        ).pack(anchor="w", fill="x")

        chips_row = ctk.CTkFrame(text_frame, fg_color="transparent")
        chips_row.pack(anchor="w", pady=(3, 0))
        for chip in _tweak_chips(chips_row, tweak):
            chip.pack(side="left", padx=(0, 6))

        ctk.CTkLabel(
            text_frame, text=tweak.description, text_color="gray", font=ctk.CTkFont(size=11),
            wraplength=620, justify="left",
        ).pack(anchor="w", pady=(3, 0))

        self.detail_label = ctk.CTkLabel(
            text_frame, text="", text_color=theme.TEXT_DIM, font=ctk.CTkFont(size=11),
            wraplength=620, justify="left",
        )
        self.blocked_label = ctk.CTkLabel(
            text_frame, text="", text_color=theme.WARNING, font=ctk.CTkFont(size=11),
            wraplength=620, justify="left",
        )
        self.refresh()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.refresh()

    def refresh(self) -> None:
        """Перечитує реальний стан твіка з реєстру й оновлює перемикач і примітки."""
        state = tweaks_core.get_state(self.tweak)
        self.switch_var.set(state)

        detail = tweaks_core.detail_text(self.tweak)
        self.detail_label.configure(text=detail)
        if detail:
            self.detail_label.pack(anchor="w", pady=(2, 0))
        else:
            self.detail_label.pack_forget()

        # Заблокований твік не можна ввімкнути, але вимкнути (повернути) можна завжди.
        reason = "" if state else tweaks_core.blocked_reason(self.tweak)
        self.blocked_label.configure(text=reason)
        if reason:
            self.blocked_label.pack(anchor="w", pady=(2, 0))
        else:
            self.blocked_label.pack_forget()

        self.switch.configure(state="disabled" if self._busy or reason else "normal")


class SettingsLinkRow(ctk.CTkFrame):
    """Налаштування без надійного ключа реєстру: пояснення й кнопка «Відкрити»."""

    def __init__(self, master, link: tweaks_core.SettingsLink):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            self, text="Відкрити", width=80, height=26, fg_color="transparent", border_width=1,
            border_color=theme.BORDER, text_color=theme.TEXT_MAIN, hover_color=theme.BG_PANEL_LIGHT,
            command=lambda: self._open(link.uri),
        ).grid(row=0, column=0, padx=(0, 10), pady=8, sticky="n")

        text_frame = ctk.CTkFrame(self, fg_color="transparent")
        text_frame.grid(row=0, column=1, sticky="ew", pady=8)
        ctk.CTkLabel(
            text_frame, text=link.title, font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
        ).pack(anchor="w")
        chips_row = ctk.CTkFrame(text_frame, fg_color="transparent")
        chips_row.pack(anchor="w", pady=(3, 0))
        _chip(chips_row, " вручну в «Параметрах» ", theme.TEXT_DIM).pack(side="left")
        ctk.CTkLabel(
            text_frame, text=link.description, text_color="gray", font=ctk.CTkFont(size=11),
            wraplength=620, justify="left",
        ).pack(anchor="w", pady=(3, 0))

    def _open(self, uri: str) -> None:
        try:
            os.startfile(uri)
        except OSError as exc:
            messagebox.showerror("Помилка", f"Не вдалося відкрити «Параметри»: {exc}", parent=self)


class ChecklistDialog(ctk.CTkToplevel):
    """Список змін із галочками перед застосуванням пресета чи відкату.

    items: (id, title, risk, effect, note, selectable). note — що саме станеться
    (або чому пункт недоступний, якщо selectable=False)."""

    def __init__(self, master, title: str, intro: str, items: list, confirm_text: str,
                 warning: str = "", danger: bool = False):
        super().__init__(master)
        self.result: list[str] | None = None
        self._confirm_text = confirm_text
        self.title(title)
        self.resizable(False, False)
        self.configure(fg_color=theme.BG_PANEL)
        self.transient(master.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        width = 560
        ctk.CTkLabel(self, text=title, font=theme.font_header(), anchor="w").pack(
            fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(self, text=intro, font=theme.font_body(), text_color=theme.TEXT_DIM, anchor="w",
                     justify="left", wraplength=width - 40).pack(fill="x", padx=20)
        if warning:
            ctk.CTkLabel(self, text=warning, font=theme.font_small(), text_color=theme.WARNING, anchor="w",
                         justify="left", wraplength=width - 40).pack(fill="x", padx=20, pady=(8, 0))

        listbox = ctk.CTkScrollableFrame(self, width=width - 60, height=min(360, 58 * len(items) + 10),
                                         fg_color=theme.BG_MAIN, corner_radius=8)
        listbox.pack(fill="both", expand=True, padx=20, pady=(12, 0))

        self._boxes: list[tuple[str, ctk.CTkCheckBox]] = []
        for tweak_id, item_title, risk, effect, note, selectable in items:
            row = ctk.CTkFrame(listbox, fg_color="transparent")
            row.pack(fill="x", pady=4)
            box = ctk.CTkCheckBox(row, text=item_title, font=theme.font_body(), command=self._update_count,
                                  checkbox_width=20, checkbox_height=20)
            box.pack(anchor="w")
            if selectable:
                box.select()
                self._boxes.append((tweak_id, box))
            else:
                box.configure(state="disabled")
            meta = ctk.CTkFrame(row, fg_color="transparent")
            meta.pack(anchor="w", padx=(28, 0), pady=(2, 0))
            _chip(meta, f" ● {_RISK_LABELS[risk]} ", _RISK_COLORS[risk]).pack(side="left", padx=(0, 6))
            _chip(meta, f" ефект: {tweaks_core.EFFECT_LABELS[effect]} ", _EFFECT_COLORS[effect]).pack(
                side="left", padx=(0, 6))
            if note:
                ctk.CTkLabel(row, text=note, font=theme.font_small(),
                             text_color=theme.TEXT_DIM if selectable else theme.WARNING, anchor="w",
                             wraplength=width - 120, justify="left").pack(anchor="w", padx=(28, 0))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(14, 18))
        style = ({"fg_color": "#a8283f", "hover_color": theme.ERROR, "text_color": "#ffffff"} if danger
                 else {"fg_color": theme.ACCENT_BLUE_DIM})
        self.confirm_button = ctk.CTkButton(buttons, text=confirm_text, width=10, height=32, corner_radius=8,
                                            command=self._confirm, **style)
        self.confirm_button.pack(side="right")
        ctk.CTkButton(buttons, text="Скасувати", width=100, height=32, corner_radius=8, fg_color="transparent",
                      border_width=1, border_color=theme.BORDER, hover_color=theme.BG_PANEL_LIGHT,
                      text_color=theme.TEXT_MAIN, command=self._cancel).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _e: self._cancel())
        self._update_count()

        self.update_idletasks()
        root = master.winfo_toplevel()
        x = root.winfo_rootx() + (root.winfo_width() - self.winfo_reqwidth()) // 2
        y = root.winfo_rooty() + (root.winfo_height() - self.winfo_reqheight()) // 4
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.after(30, self._grab)

    def _selected(self) -> list[str]:
        return [tweak_id for tweak_id, box in self._boxes if box.get()]

    def _update_count(self) -> None:
        count = len(self._selected())
        self.confirm_button.configure(text=f"{self._confirm_text} ({count})",
                                      state="normal" if count else "disabled")

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def _confirm(self) -> None:
        self.result = self._selected()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


def ask_checklist(master, title: str, intro: str, items: list, confirm_text: str,
                  warning: str = "", danger: bool = False) -> list[str] | None:
    dialog = ChecklistDialog(master, title, intro, items, confirm_text, warning, danger)
    master.wait_window(dialog)
    return dialog.result


class RegistryTweaksTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.rows: dict[str, TweakRow] = {}
        self._preset_counts: dict[str, ctk.CTkLabel] = {}
        self._preset_buttons: list[ctk.CTkButton] = []
        self._reboot_titles: set[str] = set()
        self._logoff_titles: set[str] = set()

        self._build_header()

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 10))
        self.scroll.grid_columnconfigure(0, weight=1)

        self._build_presets()
        self._build_mode_block()
        self._build_rows()
        self._refresh_presets()

        # Тип системного диска (для SysMain) — PowerShell, ~1 с: у фоні.
        bg.run_task(self, "Твіки: тип системного диска", tweaks_core.detect_system_disk,
                    on_done=lambda _kind: self._refresh_row("sysmain_off"),
                    on_error=lambda _exc: self._refresh_row("sysmain_off"))

    # ------------------------------------------------------------------ UI

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="Твіки реєстру", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkLabel(
            header,
            text=(
                "Кожна зміна — з бекапом .reg і можливістю повернути як було. "
                "Ефект оцінено чесно: більшість твіків не додає FPS, а прибирає фонові "
                "заважки й затримки."
            ),
            text_color=theme.TEXT_DIM, font=theme.font_small(), wraplength=760, justify="left", anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(2, 0))

        self.banner_label = ctk.CTkLabel(
            header, text="", text_color=theme.WARNING, font=ctk.CTkFont(size=11),
            wraplength=760, justify="left", anchor="w",
        )
        self.banner_label.grid(row=2, column=0, sticky="ew", pady=(6, 0))

    def _build_presets(self):
        ctk.CTkLabel(
            self.scroll, text="Пресети", font=ctk.CTkFont(size=14, weight="bold"), anchor="w",
        ).pack(fill="x", padx=10, pady=(4, 2))

        grid = ctk.CTkFrame(self.scroll, fg_color="transparent")
        grid.pack(fill="x", padx=10, pady=(4, 2))
        for column in range(4):
            grid.grid_columnconfigure(column, weight=1, uniform="preset")

        cards = [(key, title, text, color, self._make_preset_handler(key)) for key, title, text, color in _PRESETS]
        cards.append((
            "restore", "Повернути все як було",
            "Відкотити всі твіки, змінені PulseFPS, до стану перед першою зміною.",
            theme.ERROR, self._on_restore_clicked,
        ))
        for column, (key, title, text, color, handler) in enumerate(cards):
            card = ctk.CTkFrame(grid, corner_radius=12, fg_color=theme.BG_PANEL,
                                border_width=1, border_color=theme.BORDER)
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 5, 0 if column == 3 else 5))
            tk.Frame(card, height=3, bg=color, bd=0, highlightthickness=0).pack(fill="x", padx=14, pady=(12, 0))
            ctk.CTkLabel(card, text=title, font=theme.font_header(), anchor="w").pack(
                fill="x", padx=14, pady=(8, 2))
            ctk.CTkLabel(card, text=text, font=theme.font_small(), text_color=theme.TEXT_DIM, anchor="nw",
                         justify="left", wraplength=180, height=48).pack(fill="x", padx=14)
            count = ctk.CTkLabel(card, text="", font=theme.font_small(), text_color=color, anchor="w")
            count.pack(fill="x", padx=14, pady=(4, 0))
            self._preset_counts[key] = count
            is_restore = key == "restore"
            button = ctk.CTkButton(
                card, text="Повернути…" if is_restore else "Переглянути…", height=30, corner_radius=8,
                command=handler,
                **({"fg_color": "transparent", "border_width": 1, "border_color": "#a8283f",
                    "hover_color": theme.BG_PANEL_LIGHT, "text_color": theme.TEXT_MAIN}
                   if is_restore else {"fg_color": theme.ACCENT_BLUE_DIM}),
            )
            button.pack(fill="x", padx=14, pady=(8, 14))
            self._preset_buttons.append(button)
            if is_restore:
                self.restore_button = button

        ctk.CTkLabel(
            self.scroll,
            text="Червоні твіки («ризиковано») ніколи не входять у пресети — лише вручну, з окремим підтвердженням.",
            text_color=theme.TEXT_DIM, font=theme.font_small(), anchor="w", justify="left", wraplength=760,
        ).pack(fill="x", padx=10, pady=(6, 10))

    def _build_mode_block(self):
        block = ctk.CTkFrame(self.scroll, corner_radius=12, fg_color=theme.BG_PANEL,
                             border_width=1, border_color=theme.BORDER)
        block.pack(fill="x", padx=10, pady=(0, 10))
        block.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            block, text="Режим Windows", font=ctk.CTkFont(size=15, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 2))

        ctk.CTkLabel(
            block,
            text=(
                "Швидко перемикає вигляд Windows між «як завжди» і «максимальна "
                "швидкодія» (твіки розділу «Вигляд» нижче)."
            ),
            text_color="gray", font=ctk.CTkFont(size=11), wraplength=700, justify="left",
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 10))

        btn_row = ctk.CTkFrame(block, fg_color="transparent")
        btn_row.grid(row=2, column=0, sticky="w", padx=16, pady=(0, 14))

        self.max_perf_button = ctk.CTkButton(
            btn_row, text="Максимальна швидкодія", command=self._on_max_performance_clicked,
        )
        self.max_perf_button.pack(side="left", padx=(0, 10))

        self.restore_appearance_button = ctk.CTkButton(
            btn_row, text="Повернути гарну Windows", fg_color="transparent", border_width=1,
            command=self._on_restore_appearance_clicked,
        )
        self.restore_appearance_button.pack(side="left")

    def _build_rows(self):
        first = True
        for group in tweaks_core.GROUP_ORDER:
            tweaks = [t for t in tweaks_core.TWEAKS if t.group == group]
            links = [link for link in tweaks_core.SETTINGS_LINKS if link.group == group]
            if not tweaks and not links:
                continue
            ctk.CTkLabel(
                self.scroll, text=tweaks_core.GROUP_LABELS.get(group, group),
                font=ctk.CTkFont(size=14, weight="bold"), anchor="w",
            ).pack(fill="x", padx=10, pady=(4 if first else 16, 2), anchor="w")
            first = False

            for tweak in tweaks:
                row = TweakRow(self.scroll, tweak, self._on_row_toggle)
                row.pack(fill="x", padx=10, pady=6, anchor="w")
                self.rows[tweak.id] = row
            for link in links:
                SettingsLinkRow(self.scroll, link).pack(fill="x", padx=10, pady=6, anchor="w")

    def _refresh_row(self, tweak_id: str) -> None:
        row = self.rows.get(tweak_id)
        if row is not None:
            row.refresh()
        self._refresh_presets()

    def _refresh_presets(self) -> None:
        for key, _title, _text, _color in _PRESETS:
            pending = [t for t, reason in tweaks_core.preset_pending(key) if not reason]
            self._preset_counts[key].configure(
                text=f"Буде змінено: {len(pending)}" if pending else "Усе вже застосовано ✓"
            )
        changed = len(tweaks_core.restore_pending())
        self._preset_counts["restore"].configure(
            text=f"Змінено PulseFPS: {changed}" if changed else "Нічого повертати"
        )
        self.restore_button.configure(state="normal" if tweaks_core.has_initial_state() else "disabled")

    # --------------------------------------------------------------- toggle

    def _on_row_toggle(self, row: TweakRow):
        tweak = row.tweak
        want_enabled = row.switch_var.get()

        if want_enabled:
            reason = tweaks_core.blocked_reason(tweak)
            if reason:
                row.refresh()
                messagebox.showinfo("Недоступно", reason, parent=self)
                return
            if not self._confirm_enable(tweak):
                row.refresh()
                return

        row.set_busy(True)
        bg.run_task(
            self, f"Твік «{tweak.title}»", lambda: tweaks_core.set_tweak(tweak, want_enabled),
            on_done=lambda result: self._on_toggle_done(row, tweak, *result),
            on_error=lambda exc: self._on_toggle_done(row, tweak, False, bg.error_text(exc)),
            timeout=_APPLY_TIMEOUT_S,
        )

    def _confirm_enable(self, tweak: tweaks_core.Tweak) -> bool:
        if tweak.risk == tweaks_core.RISK_DANGER:
            return confirm_dialog.ask(
                self, f"Ризиковано: {tweak.title}",
                f"{tweak.description}\n\nЧим ви ризикуєте:\n{tweak.risk_note}\n\n"
                "PulseFPS збереже бекап .reg, і цю зміну можна буде повернути перемикачем "
                "або кнопкою «Повернути все як було».",
                "Я розумію ризик — увімкнути", danger=True,
            )
        if tweak.risk == tweaks_core.RISK_CAUTION:
            note = f"\n\nПобічний ефект: {tweak.risk_note}" if tweak.risk_note else ""
            return confirm_dialog.ask(
                self, "Твік «на свій розсуд»",
                f"«{tweak.title}»\n\n{tweak.description}{note}\n\nУвімкнути?",
                "Увімкнути",
            )
        return True

    def _on_toggle_done(self, row: TweakRow, tweak: tweaks_core.Tweak, success: bool, error: str):
        row.set_busy(False)
        self._refresh_presets()

        if success:
            self._register_requirements(tweak)
            if tweak.needs_explorer:
                self._offer_explorer_restart()
        else:
            messagebox.showerror("Помилка", error or "Не вдалося змінити твік", parent=self)

    # ---------------------------------------------------------------- пресети

    def _make_preset_handler(self, preset: str):
        return lambda: self._on_preset_clicked(preset)

    def _on_preset_clicked(self, preset: str):
        title = next(title for key, title, _t, _c in _PRESETS if key == preset)
        pending = tweaks_core.preset_pending(preset)
        if not any(not reason for _t, reason in pending):
            messagebox.showinfo(title, "Усі твіки цього пресета вже застосовано.", parent=self)
            return

        items = []
        for tweak, reason in pending:
            note = reason or tweak.risk_note
            if not reason and tweak.requires_reboot:
                note = (note + " " if note else "") + "Потрібне перезавантаження."
            items.append((tweak.id, tweak.title, tweak.risk, tweak.effect, note, not reason))

        chosen = ask_checklist(
            self, f"Пресет «{title}»",
            "Буде увімкнено позначені твіки. Зніміть галочки з того, що не потрібно. "
            "Перед зміною PulseFPS збереже бекап .reg.",
            items, "Застосувати",
            warning=_MAX_WARNING if preset == tweaks_core.PRESET_MAX else "",
        )
        if not chosen:
            return
        self._run_batch(f"Пресет «{title}»", lambda: tweaks_core.apply_preset(preset, chosen))

    # ------------------------------------------------------------------ відкат

    def _on_restore_clicked(self):
        pending = tweaks_core.restore_pending()
        if not pending:
            messagebox.showinfo("Повернути все як було", "Усі твіки вже в початковому стані.", parent=self)
            return

        items = [
            (tweak.id, tweak.title, tweak.risk, tweak.effect,
             "повернеться: " + ("увімкнено" if target else "вимкнено"), True)
            for tweak, target in pending
        ]
        chosen = ask_checklist(
            self, "Повернути все як було",
            "Позначені твіки повернуться до стану, який був перед їх першою зміною в PulseFPS.",
            items, "Повернути", danger=True,
        )
        if not chosen:
            return
        self._run_batch("Повернути все як було", lambda: tweaks_core.restore_tweaks(chosen))

    # -------------------------------------------------------- режим Windows

    def _on_max_performance_clicked(self):
        names = "\n".join(f"• {t.title}" for t in tweaks_core.get_appearance_tweaks())
        confirmed = confirm_dialog.ask(
            self, "Максимальна швидкодія",
            "Windows виглядатиме простіше: без прозорості, анімацій і тіней.\n\n"
            f"Буде увімкнено:\n{names}",
            "Увімкнути",
        )
        if confirmed:
            self._run_batch("Максимальна швидкодія", tweaks_core.apply_max_performance)

    def _on_restore_appearance_clicked(self):
        confirmed = confirm_dialog.ask(
            self, "Повернути гарну Windows",
            "Повернути вигляд Windows (прозорість, анімації, тіні) до стану, який був до перших змін?",
            "Повернути",
        )
        if confirmed:
            self._run_batch("Повернути гарну Windows", tweaks_core.restore_appearance_defaults)

    def _offer_explorer_restart(self):
        action = process_control.ask_user_action(
            self, "Застосувати зараз",
            "Щоб зміни вигляду й панелі завдань набули чинності одразу, можна перезапустити "
            "Провідник (закриються відкриті вікна папок). Зробити це зараз?\n\n"
            "Якщо відмовитесь — зміни застосуються після виходу з системи.",
            reason="Твіки → «Застосувати зараз» (перезапуск Провідника)",
        )
        if action is not None:
            bg.start_thread(self, "Перезапуск Провідника", process_control.restart_explorer, action)

    # --------------------------------------------------------------- спільне

    def _set_batch_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in self._preset_buttons + [self.max_perf_button, self.restore_appearance_button]:
            button.configure(state=state)
        for row in self.rows.values():
            row.set_busy(busy)

    def _run_batch(self, name: str, action):
        self._set_batch_busy(True)
        bg.run_task(
            self, name, action,
            on_done=self._on_batch_done,
            on_error=lambda exc: self._on_batch_done([], error=bg.error_text(exc)),
            timeout=_APPLY_TIMEOUT_S,
        )

    def _on_batch_done(self, results: list, error: str = ""):
        self._set_batch_busy(False)  # також перечитує стан усіх рядків
        self._refresh_presets()

        failed = [f"{tweak.title}: {err}" for tweak, success, err in results if not success]
        succeeded = [tweak for tweak, success, _err in results if success]
        for tweak in succeeded:
            self._register_requirements(tweak)

        if error:
            failed.append(error)
        if failed:
            messagebox.showerror(
                "Помилка", "Не вдалося застосувати деякі твіки:\n" + "\n".join(failed), parent=self
            )
        if any(tweak.needs_explorer for tweak in succeeded):
            self._offer_explorer_restart()

    def _register_requirements(self, tweak: tweaks_core.Tweak):
        if tweak.requires_reboot:
            self._reboot_titles.add(tweak.title)
        if tweak.requires_logoff:
            self._logoff_titles.add(tweak.title)
        self._update_banner()

    def _update_banner(self):
        parts = []
        if self._reboot_titles:
            parts.append("Потрібне перезавантаження: " + ", ".join(sorted(self._reboot_titles)))
        if self._logoff_titles:
            parts.append("Потрібен вихід із системи: " + ", ".join(sorted(self._logoff_titles)))
        self.banner_label.configure(text="  ·  ".join(parts))
