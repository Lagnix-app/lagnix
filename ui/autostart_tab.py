"""Вкладка «Автозапуск» — програми з реєстру Run і папок Startup, увімк./вимк."""

from tkinter import messagebox

import customtkinter as ctk

from core import admin as admin_core
from core import autostart as autostart_core
from ui.admin_status import ElevateButton

_SOURCE_ORDER = (
    autostart_core.SOURCE_HKCU,
    autostart_core.SOURCE_HKLM,
    autostart_core.SOURCE_HKLM32,
    autostart_core.SOURCE_STARTUP_USER,
    autostart_core.SOURCE_STARTUP_COMMON,
)


class AutostartRow(ctk.CTkFrame):
    """Один запис автозапуску: перемикач, назва, джерело й команда/шлях."""

    def __init__(self, master, entry: dict, on_toggle):
        super().__init__(master, fg_color="transparent")
        self.entry = entry
        self._on_toggle = on_toggle

        self.grid_columnconfigure(1, weight=1)

        blocked = entry["requires_admin"] and not admin_core.is_admin()

        self.switch_var = ctk.BooleanVar(value=entry["enabled"])
        self.switch = ctk.CTkSwitch(
            self, text="", variable=self.switch_var, width=40,
            command=lambda: self._on_toggle(self),
        )
        self.switch.grid(row=0, column=0, padx=(0, 10), pady=6, sticky="n")
        if blocked:
            self.switch.configure(state="disabled")

        text_frame = ctk.CTkFrame(self, fg_color="transparent")
        text_frame.grid(row=0, column=1, sticky="ew", pady=6)

        ctk.CTkLabel(text_frame, text=entry["name"], font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w"
        )
        ctk.CTkLabel(
            text_frame, text=entry["command"], text_color="gray", font=ctk.CTkFont(size=11),
            wraplength=560, justify="left",
        ).pack(anchor="w")

        if blocked:
            ctk.CTkLabel(
                text_frame, text="Потрібні права адміністратора для зміни цього запису",
                text_color="#e0a52f", font=ctk.CTkFont(size=11),
            ).pack(anchor="w", pady=(4, 0))
            ElevateButton(text_frame).pack(anchor="w", pady=(4, 0))

    def set_busy(self, busy: bool) -> None:
        blocked = self.entry["requires_admin"] and not admin_core.is_admin()
        self.switch.configure(state="disabled" if busy or blocked else "normal")

    def revert(self) -> None:
        self.switch_var.set(self.entry["enabled"])


class AutostartTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.rows: list[AutostartRow] = []

        self._build_header()

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=6)
        self.scroll.grid_columnconfigure(0, weight=1)

        self._load()

    # ------------------------------------------------------------------ UI

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="Автозапуск", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkButton(header, text="Оновити", width=100, command=self._load).grid(
            row=0, column=1, sticky="e"
        )

        self.status_label = ctk.CTkLabel(
            header,
            text="Програми, які запускаються разом з Windows: реєстр і папка «Автозавантаження».",
            text_color="gray",
        )
        self.status_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    # ----------------------------------------------------------------- load

    def _load(self):
        for child in self.scroll.winfo_children():
            child.destroy()
        self.rows = []

        entries = autostart_core.list_entries()
        if not entries:
            ctk.CTkLabel(
                self.scroll, text="Не знайдено жодної програми автозапуску.", text_color="gray"
            ).pack(padx=10, pady=20, anchor="w")
            self.status_label.configure(text="Знайдено записів: 0")
            return

        by_source: dict[str, list[dict]] = {}
        for entry in entries:
            by_source.setdefault(entry["source"], []).append(entry)

        for source in _SOURCE_ORDER:
            source_entries = by_source.get(source)
            if not source_entries:
                continue

            frame = ctk.CTkFrame(self.scroll, corner_radius=10)
            frame.pack(fill="x", pady=6)

            ctk.CTkLabel(
                frame, text=autostart_core.SOURCE_LABELS[source], font=ctk.CTkFont(size=14, weight="bold")
            ).pack(padx=14, pady=(10, 4), anchor="w")

            for entry in source_entries:
                row = AutostartRow(frame, entry, self._on_row_toggle)
                row.pack(fill="x", padx=14, pady=4, anchor="w")
                self.rows.append(row)

            ctk.CTkFrame(frame, fg_color="transparent", height=4).pack()

        self.status_label.configure(text=f"Знайдено записів: {len(entries)}")

    # --------------------------------------------------------------- toggle

    def _on_row_toggle(self, row: AutostartRow):
        entry = row.entry
        want_enabled = row.switch_var.get()
        if want_enabled == entry["enabled"]:
            return

        row.set_busy(True)

        if want_enabled:
            success, error = autostart_core.enable_entry(entry["id"])
        else:
            success, error = autostart_core.disable_entry(entry)

        if success:
            entry["enabled"] = want_enabled
            row.set_busy(False)
        else:
            row.revert()
            row.set_busy(False)
            messagebox.showerror("Помилка", error or "Не вдалося змінити стан автозапуску", parent=self)
