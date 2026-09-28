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

_IMPACT_COLORS = {
    "висока": "#e5484d",
    "середня": "#e0a52f",
    "низька": "#8a8a8a",
}


class AutostartRow(ctk.CTkFrame):
    """Один запис автозапуску: перемикач, назва, джерело й команда/шлях."""

    def __init__(self, master, entry: dict, on_toggle, on_open_location):
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

        title_row = ctk.CTkFrame(text_frame, fg_color="transparent")
        title_row.pack(anchor="w", fill="x")

        name_color = "gray" if entry["is_system"] else None
        name_label = ctk.CTkLabel(
            title_row, text=entry["display_name"], font=ctk.CTkFont(size=13, weight="bold"),
        )
        if name_color:
            name_label.configure(text_color=name_color)
        name_label.pack(side="left")

        impact = entry.get("impact", "низька")
        ctk.CTkLabel(
            title_row, text=f"  ·  Вплив: {impact}", font=ctk.CTkFont(size=11),
            text_color=_IMPACT_COLORS.get(impact, "gray"),
        ).pack(side="left")

        if entry["is_system"]:
            ctk.CTkLabel(
                title_row, text="  ·  системний", font=ctk.CTkFont(size=11), text_color="gray"
            ).pack(side="left")

        if entry.get("publisher"):
            ctk.CTkLabel(
                text_frame, text=entry["publisher"], text_color="gray", font=ctk.CTkFont(size=11),
            ).pack(anchor="w")

        ctk.CTkLabel(
            text_frame, text=entry["command"], text_color="gray", font=ctk.CTkFont(size=11),
            wraplength=520, justify="left",
        ).pack(anchor="w")

        if entry["is_anticheat"]:
            ctk.CTkLabel(
                text_frame, text=autostart_core.ANTICHEAT_WARNING, text_color="#e0a52f",
                font=ctk.CTkFont(size=11), wraplength=520, justify="left",
            ).pack(anchor="w", pady=(2, 0))

        if blocked:
            ctk.CTkLabel(
                text_frame, text="Потрібні права адміністратора для зміни цього запису",
                text_color="#e0a52f", font=ctk.CTkFont(size=11),
            ).pack(anchor="w", pady=(4, 0))
            ElevateButton(text_frame).pack(anchor="w", pady=(4, 0))

        self.open_location_button = ctk.CTkButton(
            self, text="Відкрити розташування", width=150, height=26,
            font=ctk.CTkFont(size=11), command=lambda: on_open_location(entry),
        )
        self.open_location_button.grid(row=0, column=2, padx=(10, 0), pady=6, sticky="n")
        if not entry.get("resolved_path"):
            self.open_location_button.configure(state="disabled")

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
        self.all_entries: list[dict] = []

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

        controls = ctk.CTkFrame(header, fg_color="transparent")
        controls.grid(row=0, column=1, sticky="e")

        self.search_var = ctk.StringVar()
        self.search_entry = ctk.CTkEntry(
            controls, placeholder_text="Пошук за назвою", width=220, textvariable=self.search_var,
        )
        self.search_entry.pack(side="left", padx=(0, 10))
        self.search_var.trace_add("write", lambda *_a: self._render())

        ctk.CTkButton(controls, text="Оновити", width=100, command=self._load).pack(side="left")

        self.status_label = ctk.CTkLabel(header, text="", text_color="gray")
        self.status_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    # ----------------------------------------------------------------- load

    def _load(self):
        self.all_entries = autostart_core.list_entries()
        self._render()

    def _render(self):
        for child in self.scroll.winfo_children():
            child.destroy()
        self.rows = []

        query = self.search_var.get().strip().lower()
        entries = [
            e for e in self.all_entries
            if not query
            or query in e["display_name"].lower()
            or query in e["name"].lower()
            or query in (e.get("publisher") or "").lower()
        ]

        if not self.all_entries:
            ctk.CTkLabel(
                self.scroll, text="Не знайдено жодної програми автозапуску.", text_color="gray"
            ).pack(padx=10, pady=20, anchor="w")
            self.status_label.configure(text="Знайдено записів: 0")
            return

        if not entries:
            ctk.CTkLabel(
                self.scroll, text="Нічого не знайдено за пошуком.", text_color="gray"
            ).pack(padx=10, pady=20, anchor="w")
        else:
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
                    row = AutostartRow(frame, entry, self._on_row_toggle, self._on_open_location)
                    row.pack(fill="x", padx=14, pady=4, anchor="w")
                    self.rows.append(row)

                ctk.CTkFrame(frame, fg_color="transparent", height=4).pack()

        enabled_count = sum(1 for e in self.all_entries if e["enabled"])
        total = len(self.all_entries)
        status = f"Увімкнено {enabled_count} з {total}"
        if query:
            status += f"  ·  Знайдено за пошуком: {len(entries)}"
        self.status_label.configure(text=status)

    # --------------------------------------------------------------- toggle

    def _on_row_toggle(self, row: AutostartRow):
        entry = row.entry
        want_enabled = row.switch_var.get()
        if want_enabled == entry["enabled"]:
            return

        if not want_enabled and not self._confirm_disable(entry):
            row.revert()
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

    def _confirm_disable(self, entry: dict) -> bool:
        name = entry["display_name"]
        if entry["is_anticheat"]:
            return messagebox.askyesno(
                "Підтвердження",
                f"«{name}» пов'язаний з античитом.\n{autostart_core.ANTICHEAT_WARNING}\n\nВимкнути автозапуск?",
                parent=self,
            )
        if entry["is_system"]:
            return messagebox.askyesno(
                "Підтвердження",
                f"«{name}» — системний запис Windows. Вимикати його зазвичай не потрібно.\n\n"
                "Вимкнути автозапуск?",
                parent=self,
            )
        return True

    # --------------------------------------------------------- open location

    def _on_open_location(self, entry: dict):
        success, error = autostart_core.open_location(entry)
        if not success:
            messagebox.showerror("Помилка", error or "Не вдалося відкрити розташування файлу", parent=self)
