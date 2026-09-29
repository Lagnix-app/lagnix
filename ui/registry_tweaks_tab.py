"""Вкладка «Твіки реєстру» — набір фіксованих ігрових твіків з core/tweaks.py."""

import threading
from tkinter import messagebox

import customtkinter as ctk

from core import process_control
from ui import bg
from core import tweaks as tweaks_core

_RISK_LABELS = {
    tweaks_core.RISK_SAFE: "безпечно",
    tweaks_core.RISK_CAUTION: "на свій розсуд",
}
_RISK_COLORS = {
    tweaks_core.RISK_SAFE: "#2ee59d",
    tweaks_core.RISK_CAUTION: "#e0a52f",
}


class TweakRow(ctk.CTkFrame):
    """Один твік: перемикач, назва, позначка ризику, опис і примітки."""

    def __init__(self, master, tweak: tweaks_core.Tweak, on_toggle):
        super().__init__(master, fg_color="transparent")
        self.tweak = tweak
        self._on_toggle = on_toggle

        self.grid_columnconfigure(1, weight=1)

        self.switch_var = ctk.BooleanVar(value=tweaks_core.get_state(tweak))
        self.switch = ctk.CTkSwitch(
            self, text="", variable=self.switch_var, width=40,
            command=lambda: self._on_toggle(self),
        )
        self.switch.grid(row=0, column=0, padx=(0, 10), pady=8, sticky="n")

        text_frame = ctk.CTkFrame(self, fg_color="transparent")
        text_frame.grid(row=0, column=1, sticky="ew", pady=8)

        title_row = ctk.CTkFrame(text_frame, fg_color="transparent")
        title_row.pack(anchor="w", fill="x")

        ctk.CTkLabel(
            title_row, text=tweak.title, font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")

        risk_color = _RISK_COLORS[tweak.risk]
        ctk.CTkLabel(
            title_row, text=f"  ·  {_RISK_LABELS[tweak.risk]}", font=ctk.CTkFont(size=11),
            text_color=risk_color,
        ).pack(side="left")

        if tweak.requires_reboot:
            ctk.CTkLabel(
                title_row, text="  ·  потребує перезавантаження", font=ctk.CTkFont(size=11),
                text_color="gray",
            ).pack(side="left")
        elif tweak.requires_logoff:
            ctk.CTkLabel(
                title_row, text="  ·  потребує виходу з системи", font=ctk.CTkFont(size=11),
                text_color="gray",
            ).pack(side="left")

        ctk.CTkLabel(
            text_frame, text=tweak.description, text_color="gray", font=ctk.CTkFont(size=11),
            wraplength=560, justify="left",
        ).pack(anchor="w", pady=(2, 0))

    def set_busy(self, busy: bool) -> None:
        self.switch.configure(state="disabled" if busy else "normal")

    def sync(self) -> None:
        """Перечитує реальний стан твіка з реєстру і оновлює перемикач."""
        self.switch_var.set(tweaks_core.get_state(self.tweak))


class RegistryTweaksTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self.rows: dict[str, TweakRow] = {}
        self._reboot_titles: set[str] = set()
        self._logoff_titles: set[str] = set()

        self._build_header()
        self._build_mode_block()

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=2, column=0, sticky="nsew", padx=6, pady=(0, 10))
        self.scroll.grid_columnconfigure(0, weight=1)

        self._build_rows()
        self._update_restore_button_state()

    # ------------------------------------------------------------------ UI

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="Твіки реєстру", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )

        controls = ctk.CTkFrame(header, fg_color="transparent")
        controls.grid(row=0, column=1, sticky="e")

        self.restore_button = ctk.CTkButton(
            controls, text="Повернути все як було", width=180,
            fg_color="#a8283f", hover_color="#ff5c7a", command=self._on_restore_clicked,
        )
        self.restore_button.pack(side="left", padx=(0, 10))

        self.recommended_button = ctk.CTkButton(
            controls, text="Застосувати рекомендовані", width=200,
            command=self._on_recommended_clicked,
        )
        self.recommended_button.pack(side="left")

        self.banner_label = ctk.CTkLabel(
            header, text="", text_color="#e0a52f", font=ctk.CTkFont(size=11),
            wraplength=760, justify="left", anchor="w",
        )
        self.banner_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    def _build_mode_block(self):
        block = ctk.CTkFrame(self, corner_radius=10)
        block.grid(row=1, column=0, padx=20, pady=(0, 14), sticky="ew")
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
        current_group = None
        for tweak in tweaks_core.TWEAKS:
            if tweak.group != current_group:
                current_group = tweak.group
                header = ctk.CTkLabel(
                    self.scroll,
                    text=tweaks_core.GROUP_LABELS.get(current_group, current_group),
                    font=ctk.CTkFont(size=14, weight="bold"),
                    anchor="w",
                )
                top_pad = 4 if len(self.rows) == 0 else 16
                header.pack(fill="x", padx=10, pady=(top_pad, 2), anchor="w")

            row = TweakRow(self.scroll, tweak, self._on_row_toggle)
            row.pack(fill="x", padx=10, pady=6, anchor="w")
            self.rows[tweak.id] = row

    # --------------------------------------------------------------- toggle

    def _on_row_toggle(self, row: TweakRow):
        tweak = row.tweak
        want_enabled = row.switch_var.get()

        if want_enabled and tweak.risk == tweaks_core.RISK_CAUTION:
            confirmed = messagebox.askyesno(
                "Підтвердження",
                f"«{tweak.title}»\n\n{tweak.description}\n\n"
                "Цей твік позначено як «на свій розсуд». Увімкнути його?",
                parent=self,
            )
            if not confirmed:
                row.sync()
                return

        row.set_busy(True)

        def worker():
            success, error = tweaks_core.set_tweak(tweak, want_enabled)
            bg.ui_call(self, self._on_toggle_done, row, tweak, success, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_toggle_done(self, row: TweakRow, tweak: tweaks_core.Tweak, success: bool, error: str):
        row.set_busy(False)
        row.sync()

        if success:
            self._register_requirements(tweak)
            self._update_restore_button_state()
            if tweak.group == tweaks_core.GROUP_APPEARANCE:
                self._offer_explorer_restart()
        else:
            messagebox.showerror("Помилка", error or "Не вдалося змінити твік", parent=self)

    # ------------------------------------------------------------ рекомендовані

    def _on_recommended_clicked(self):
        pending = [t for t in tweaks_core.get_recommended_tweaks() if not tweaks_core.get_state(t)]
        if not pending:
            messagebox.showinfo("Інфо", "Усі рекомендовані твіки вже увімкнено.", parent=self)
            return

        names = "\n".join(f"• {t.title}" for t in pending)
        confirmed = messagebox.askyesno(
            "Застосувати рекомендовані твіки",
            f"Буде увімкнено:\n\n{names}\n\nПродовжити?",
            parent=self,
        )
        if not confirmed:
            return

        self.recommended_button.configure(state="disabled")

        def worker():
            results = tweaks_core.apply_recommended()
            bg.ui_call(self, self._on_batch_done, results, self.recommended_button)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ відкат

    def _on_restore_clicked(self):
        confirmed = messagebox.askyesno(
            "Підтвердження",
            "Повернути всі змінені твіки до стану, який був перед першою зміною?",
            parent=self,
        )
        if not confirmed:
            return

        self.restore_button.configure(state="disabled")

        def worker():
            results = tweaks_core.restore_initial_state()
            bg.ui_call(self, self._on_batch_done, results, self.restore_button)

        threading.Thread(target=worker, daemon=True).start()

    # -------------------------------------------------------- режим Windows

    def _on_max_performance_clicked(self):
        names = "\n".join(f"• {t.title}" for t in tweaks_core.get_appearance_tweaks())
        confirmed = messagebox.askyesno(
            "Максимальна швидкодія",
            "Windows виглядатиме простіше: без прозорості, анімацій і тіней.\n\n"
            f"Буде увімкнено:\n{names}\n\nПродовжити?",
            parent=self,
        )
        if not confirmed:
            return
        self._run_appearance_batch(tweaks_core.apply_max_performance)

    def _on_restore_appearance_clicked(self):
        confirmed = messagebox.askyesno(
            "Повернути гарну Windows",
            "Повернути вигляд Windows (прозорість, анімації, тіні) до стану, який "
            "був до перших змін?",
            parent=self,
        )
        if not confirmed:
            return
        self._run_appearance_batch(tweaks_core.restore_appearance_defaults)

    def _run_appearance_batch(self, action):
        self.max_perf_button.configure(state="disabled")
        self.restore_appearance_button.configure(state="disabled")

        def worker():
            results = action()
            bg.ui_call(self, self._on_appearance_batch_done, results)

        threading.Thread(target=worker, daemon=True).start()

    def _on_appearance_batch_done(self, results: list):
        self._on_batch_done(results, self.max_perf_button)
        self.restore_appearance_button.configure(state="normal")
        if results:
            self._offer_explorer_restart()

    def _offer_explorer_restart(self):
        action = process_control.ask_user_action(
            self, "Застосувати зараз",
            "Щоб зміни вигляду набули чинності одразу, можна перезапустити "
            "Провідник (закриються відкриті вікна папок). Зробити це зараз?\n\n"
            "Якщо відмовитесь — зміни застосуються після виходу з системи.",
            reason="Твіки → «Застосувати зараз» (перезапуск Провідника)",
        )
        if action is not None:
            threading.Thread(target=process_control.restart_explorer, args=(action,), daemon=True).start()

    # --------------------------------------------------------------- спільне

    def _on_batch_done(self, results: list, button: ctk.CTkButton):
        failed = []
        for tweak, success, error in results:
            row = self.rows.get(tweak.id)
            if row is not None:
                row.sync()
            if success:
                self._register_requirements(tweak)
            else:
                failed.append(f"{tweak.title}: {error}")

        button.configure(state="normal")
        self._update_restore_button_state()

        if failed:
            messagebox.showerror(
                "Помилка", "Не вдалося застосувати деякі твіки:\n" + "\n".join(failed), parent=self
            )

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

    def _update_restore_button_state(self):
        self.restore_button.configure(
            state="normal" if tweaks_core.has_initial_state() else "disabled"
        )
