"""Вкладка «Програми» — встановлені програми з реєстру, пошук, сортування, видалення."""

import threading
from tkinter import messagebox

import customtkinter as ctk

from core import installed_programs as programs_core
from core.cleanup import format_size

SORT_OPTIONS = {
    "За розміром": lambda p: -p["size_bytes"],
    "За назвою": lambda p: p["name"].lower(),
    "За датою встановлення": lambda p: p["install_date"] or "",
}


class ProgramRow(ctk.CTkFrame):
    def __init__(self, master, program: dict, on_uninstall):
        super().__init__(master, corner_radius=8)
        self.program = program
        self._on_uninstall = on_uninstall

        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_columnconfigure(2, weight=1)
        self.grid_columnconfigure(3, weight=1)
        self.grid_columnconfigure(4, weight=0)

        name_text = program["name"]
        if program.get("version"):
            name_text += f" ({program['version']})"

        ctk.CTkLabel(self, text=name_text, anchor="w").grid(row=0, column=0, padx=(10, 4), pady=8, sticky="w")
        ctk.CTkLabel(self, text=program.get("publisher") or "—", text_color="gray", anchor="w").grid(
            row=0, column=1, padx=4, pady=8, sticky="w"
        )

        size_text = format_size(program["size_bytes"]) if program["size_bytes"] else "—"
        ctk.CTkLabel(self, text=size_text, anchor="w").grid(row=0, column=2, padx=4, pady=8, sticky="w")
        ctk.CTkLabel(self, text=program.get("install_date") or "—", anchor="w").grid(
            row=0, column=3, padx=4, pady=8, sticky="w"
        )

        self.uninstall_button = ctk.CTkButton(
            self, text="Видалити", width=100, fg_color="#8b2c2c", hover_color="#a83a3a",
            command=self._uninstall,
        )
        self.uninstall_button.grid(row=0, column=4, padx=(4, 10), pady=8)

    def _uninstall(self):
        self._on_uninstall(self)


class ProgramsTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.all_programs: list[dict] = []
        self.rows: list[ProgramRow] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        self._build_header()
        self._build_controls()
        self._build_list_header()
        self._build_list()

        self._load()

    # ------------------------------------------------------------------ UI

    def _build_header(self):
        ctk.CTkLabel(self, text="Програми", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, padx=20, pady=(20, 5), sticky="w"
        )
        self.status_label = ctk.CTkLabel(self, text="Завантаження списку програм...", text_color="gray")
        self.status_label.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="w")

    def _build_controls(self):
        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=2, column=0, padx=20, pady=(0, 10), sticky="ew")

        self.search_entry = ctk.CTkEntry(controls, placeholder_text="Пошук за назвою або видавцем", width=280)
        self.search_entry.pack(side="left", padx=(0, 10))
        self.search_entry.bind("<KeyRelease>", lambda _e: self._refresh_list())

        self.sort_var = ctk.StringVar(value="За розміром")
        sort_menu = ctk.CTkOptionMenu(
            controls, variable=self.sort_var, values=list(SORT_OPTIONS.keys()),
            command=lambda _v: self._refresh_list(),
        )
        sort_menu.pack(side="left", padx=(0, 10))

        self.refresh_button = ctk.CTkButton(controls, text="Оновити", width=90, command=self._load)
        self.refresh_button.pack(side="left")

    def _build_list_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=3, column=0, padx=20, pady=(0, 4), sticky="ew")
        header.grid_columnconfigure(0, weight=3)
        header.grid_columnconfigure(1, weight=2)
        header.grid_columnconfigure(2, weight=1)
        header.grid_columnconfigure(3, weight=1)
        header.grid_columnconfigure(4, weight=0)

        for i, text in enumerate(("Назва", "Видавець", "Розмір", "Дата встановлення", "")):
            ctk.CTkLabel(header, text=text, text_color="gray", font=ctk.CTkFont(size=11, weight="bold")).grid(
                row=0, column=i, padx=(10 if i == 0 else 4, 4), sticky="w"
            )

    def _build_list(self):
        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list_frame.grid(row=4, column=0, padx=20, pady=(0, 20), sticky="nsew")
        self.list_frame.grid_columnconfigure(0, weight=1)

    # -------------------------------------------------------------- load

    def _load(self):
        self.refresh_button.configure(state="disabled")
        self.status_label.configure(text="Завантаження списку програм...")

        def worker():
            programs = programs_core.list_installed_programs()
            self.after(0, self._on_loaded, programs)

        threading.Thread(target=worker, daemon=True).start()

    def _on_loaded(self, programs: list[dict]):
        if not self.winfo_exists():
            return
        self.refresh_button.configure(state="normal")
        self.all_programs = programs
        self.status_label.configure(text=f"Знайдено програм: {len(programs)}")
        self._refresh_list()

    def _refresh_list(self):
        for row in self.rows:
            row.destroy()
        self.rows = []

        query = self.search_entry.get().strip().lower()
        filtered = [
            p for p in self.all_programs
            if not query or query in p["name"].lower() or query in (p.get("publisher") or "").lower()
        ]

        key_func = SORT_OPTIONS.get(self.sort_var.get(), SORT_OPTIONS["За розміром"])
        filtered.sort(key=key_func)

        for program in filtered:
            row = ProgramRow(self.list_frame, program, self._uninstall)
            row.pack(fill="x", pady=3)
            self.rows.append(row)

    # -------------------------------------------------------- uninstall

    def _uninstall(self, row: ProgramRow):
        program = row.program
        confirmed = messagebox.askyesno(
            "Підтвердження",
            f"Видалити «{program['name']}»?\n\nЗапуститься офіційний майстер видалення програми.",
            parent=self,
        )
        if not confirmed:
            return

        ok, error = programs_core.uninstall_program(program["uninstall_string"])
        if not ok:
            messagebox.showerror("Помилка", f"Не вдалося запустити видалення:\n{error}", parent=self)
            return

        messagebox.showinfo(
            "Видалення розпочато",
            "Дотримуйтесь інструкцій майстра видалення. Після завершення натисніть «Оновити».",
            parent=self,
        )
