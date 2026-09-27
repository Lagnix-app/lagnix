"""Вкладка «Програми» — таблиця встановлених програм із реєстру: пошук,
сортування по колонках, видалення, відкриття папки встановлення.
"""

import os
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

import customtkinter as ctk

from core import installed_programs as programs_core
from core.cleanup import format_size
from ui.widgets.cleaner_bot_dialog import CleanerBotDialog

COLUMN_LABELS = {
    "name": "Назва",
    "publisher": "Видавець",
    "size": "Розмір",
    "date": "Дата встановлення",
}
COLUMN_WIDTHS = {"name": 360, "publisher": 180, "size": 100, "date": 130, "action": 100}
NAME_PADDING_PX = 24


def _configure_dark_treeview_style() -> str:
    """Налаштовує ttk.Style під темну тему PulseFPS; повертає ім'я стилю таблиці."""
    style = ttk.Style()
    style.theme_use("clam")

    style.configure(
        "Programs.Treeview",
        background="#242424",
        fieldbackground="#242424",
        foreground="#dce4ee",
        rowheight=30,
        borderwidth=0,
        font=("Segoe UI", 11),
    )
    style.map(
        "Programs.Treeview",
        background=[("selected", "#1f5c8b")],
        foreground=[("selected", "#ffffff")],
    )
    style.configure(
        "Programs.Treeview.Heading",
        background="#1a1a1a",
        foreground="#a0a0a0",
        relief="flat",
        font=("Segoe UI", 10, "bold"),
    )
    style.map("Programs.Treeview.Heading", background=[("active", "#333333")])
    style.layout("Programs.Treeview", style.layout("Treeview"))

    style.configure(
        "Programs.Vertical.TScrollbar",
        background="#333333",
        troughcolor="#1a1a1a",
        bordercolor="#1a1a1a",
        arrowcolor="#dce4ee",
        relief="flat",
    )
    return "Programs.Treeview"


class ProgramsTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.all_programs: list[dict] = []
        self._by_key: dict[str, dict] = {}
        self._displayed: dict[str, dict] = {}
        self._sort_key = "size"
        self._sort_reverse = True

        self._tooltip_window = None
        self._tooltip_row = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._name_font = tkfont.Font(family="Segoe UI", size=11)
        self._uninstall_dialog = None

        self._build_header()
        self._build_controls()
        self._build_table()
        self._build_footer()

        self.bind("<Destroy>", self._on_destroy)

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

        self.refresh_button = ctk.CTkButton(controls, text="Оновити", width=90, command=self._load)
        self.refresh_button.pack(side="left")

    def _build_table(self):
        style_name = _configure_dark_treeview_style()

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.grid(row=3, column=0, padx=20, pady=(0, 10), sticky="nsew")
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(0, weight=1)

        columns = ("name", "publisher", "size", "date", "action")
        self.tree = ttk.Treeview(
            container, columns=columns, show="headings", style=style_name, selectmode="browse"
        )

        for col in ("name", "publisher", "size", "date"):
            anchor = "w" if col in ("name", "publisher") else "center" if col == "date" else "e"
            self.tree.heading(col, text=COLUMN_LABELS[col], command=lambda c=col: self._on_header_click(c))
            self.tree.column(
                col, width=COLUMN_WIDTHS[col], anchor=anchor, stretch=(col == "name")
            )

        self.tree.heading("action", text="")
        self.tree.column("action", width=COLUMN_WIDTHS["action"], anchor="center", stretch=False)

        self.tree.tag_configure("evenrow", background="#242424")
        self.tree.tag_configure("oddrow", background="#2a2a2a")

        scrollbar = ttk.Scrollbar(
            container, orient="vertical", command=self.tree.yview, style="Programs.Vertical.TScrollbar"
        )
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Leave>", self._hide_tooltip)

        self._name_max_px = COLUMN_WIDTHS["name"] - NAME_PADDING_PX

    def _build_footer(self):
        self.total_label = ctk.CTkLabel(self, text="", text_color="gray")
        self.total_label.grid(row=4, column=0, padx=20, pady=(0, 16), sticky="w")

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
        self._by_key = {p["key"]: p for p in programs}
        self._refresh_list()
        self._start_size_computation()

    def _start_size_computation(self):
        """Для програм без EstimatedSize і без даних Steam рахує розмір теки
        встановлення у фоновому потоці (послідовно, щоб не навантажувати диск
        паралельними обходами), показуючи "рахую..." доки триває підрахунок.
        """
        pending = [
            p for p in self.all_programs
            if p["size_bytes"] == 0 and p.get("size_source") is None and p.get("install_folder")
        ]
        if not pending:
            return

        to_compute = []
        for program in pending:
            cached = programs_core.cached_folder_size(program["install_folder"])
            if cached is not None:
                program["size_bytes"] = cached
                program["size_source"] = "folder"
            else:
                program["size_source"] = "computing"
                to_compute.append(program)

        self._refresh_list()
        if not to_compute:
            return

        def worker():
            for program in to_compute:
                if not self.winfo_exists():
                    return
                size = programs_core.compute_folder_size(program["install_folder"])
                self.after(0, self._on_size_computed, program["key"], size)

        threading.Thread(target=worker, daemon=True).start()

    def _on_size_computed(self, key: str, size_bytes: int):
        if not self.winfo_exists():
            return
        program = self._by_key.get(key)
        if not program:
            return
        program["size_bytes"] = size_bytes
        program["size_source"] = "folder"
        self._apply_row_update(key)

    def _apply_row_update(self, key: str):
        program = self._displayed.get(key)
        if program is not None:
            self.tree.item(key, values=self._row_values(program))
        self._update_total_label()

    # -------------------------------------------------------------- sort

    def _on_header_click(self, col: str):
        if self._sort_key == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_key = col
            self._sort_reverse = False
        self._refresh_list()

    def _sort_field(self, col: str, program: dict):
        if col == "name":
            return False, program["name"].lower()
        if col == "publisher":
            publisher = program.get("publisher") or ""
            return not publisher, publisher.lower()
        if col == "size":
            return not program["size_bytes"], program["size_bytes"]
        if col == "date":
            date_obj = program.get("install_date")
            return date_obj is None, date_obj
        return False, ""

    def _sorted_programs(self, programs: list[dict]) -> list[dict]:
        col = self._sort_key
        if not col:
            return programs

        non_empty = [p for p in programs if not self._sort_field(col, p)[0]]
        empty = [p for p in programs if self._sort_field(col, p)[0]]
        non_empty.sort(key=lambda p: self._sort_field(col, p)[1], reverse=self._sort_reverse)
        return non_empty + empty

    def _update_headers(self):
        for col, label in COLUMN_LABELS.items():
            text = label
            if self._sort_key == col:
                text += " ▼" if self._sort_reverse else " ▲"
            self.tree.heading(col, text=text)

    # ------------------------------------------------------------ render

    def _truncate(self, text: str, max_px: int) -> str:
        if self._name_font.measure(text) <= max_px:
            return text

        ellipsis = "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._name_font.measure(text[:mid] + ellipsis) <= max_px:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + ellipsis

    def _row_values(self, program: dict) -> tuple:
        name_display = self._truncate(program["name"], self._name_max_px)
        publisher = program.get("publisher") or "—"

        source = program.get("size_source")
        if source == "computing":
            size_text = "рахую…"
        elif program["size_bytes"]:
            size_text = format_size(program["size_bytes"])
            if source in ("folder", "steam"):
                size_text = f"~{size_text}"
        else:
            size_text = "—"

        date_obj = program.get("install_date")
        date_text = date_obj.strftime("%Y-%m-%d") if date_obj else "—"
        return (name_display, publisher, size_text, date_text, "Видалити")

    def _refresh_list(self):
        query = self.search_entry.get().strip().lower()
        filtered = [
            p for p in self.all_programs
            if not query or query in p["name"].lower() or query in (p.get("publisher") or "").lower()
        ]
        filtered = self._sorted_programs(filtered)

        self._hide_tooltip()
        self.tree.delete(*self.tree.get_children())
        self._displayed = {}

        for i, program in enumerate(filtered):
            row_tag = "evenrow" if i % 2 == 0 else "oddrow"
            iid = program["key"]
            self.tree.insert("", "end", iid=iid, values=self._row_values(program), tags=(row_tag,))
            self._displayed[iid] = program

        self._update_headers()
        self.status_label.configure(text=f"Знайдено програм: {len(filtered)}")
        self._update_total_label()

    def _update_total_label(self):
        total_size = sum(p["size_bytes"] for p in self._displayed.values())
        approx = any(p.get("size_source") in ("folder", "steam") for p in self._displayed.values())
        prefix = "~" if approx else ""
        self.total_label.configure(
            text=f"Загальний розмір: {prefix}{format_size(total_size)} ({len(self._displayed)} програм у списку)"
        )

    # ----------------------------------------------------------- actions

    def _on_tree_click(self, event):
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        if not row_id or col_id != "#5":
            return
        self._uninstall(row_id)

    def _on_tree_double_click(self, event):
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        if not row_id or col_id == "#5":
            return
        self._open_install_folder(row_id)

    def _uninstall(self, row_id: str):
        program = self._displayed.get(row_id)
        if not program:
            return

        confirmed = messagebox.askyesno(
            "Підтвердження",
            f"Видалити «{program['name']}»?\n\nЗапуститься офіційний майстер видалення програми.",
            parent=self,
        )
        if not confirmed:
            return

        process, error = programs_core.uninstall_program(program["uninstall_string"])
        if process is None:
            messagebox.showerror("Помилка", f"Не вдалося запустити видалення:\n{error}", parent=self)
            return

        key, name = program["key"], program["name"]
        self._uninstall_dialog = CleanerBotDialog(
            self.winfo_toplevel(), title="Видалення програми", show_freed_counter=False
        )
        self._uninstall_dialog.start(f"Видаляю {name}…")
        self._uninstall_dialog.set_indeterminate(True)

        def watch():
            while process.poll() is None:
                if not self.winfo_exists():
                    return
                time.sleep(0.4)
            if self.winfo_exists():
                self.after(0, self._on_uninstall_process_done, key, name)

        threading.Thread(target=watch, daemon=True).start()

    def _on_uninstall_process_done(self, key: str, name: str):
        if not self.winfo_exists():
            return

        def worker():
            programs = programs_core.list_installed_programs()
            if self.winfo_exists():
                self.after(0, self._on_uninstall_checked, key, name, programs)

        threading.Thread(target=worker, daemon=True).start()

    def _on_uninstall_checked(self, key: str, name: str, programs: list[dict]):
        if not self.winfo_exists():
            return

        still_present = any(p["key"] == key for p in programs)
        if self._uninstall_dialog is not None:
            if still_present:
                self._uninstall_dialog.finish("Видалення скасовано або не завершено", success=False)
            else:
                self._uninstall_dialog.finish(f"Готово! {name} видалено", success=True)
            self._uninstall_dialog = None

        self.all_programs = programs
        self._by_key = {p["key"]: p for p in programs}
        self._refresh_list()
        self._start_size_computation()

    def _open_install_folder(self, row_id: str):
        program = self._displayed.get(row_id)
        if not program:
            return

        folder = program.get("install_folder")
        if not folder:
            messagebox.showinfo(
                "Розташування невідоме",
                f"Не вдалося визначити папку встановлення для «{program['name']}».",
                parent=self,
            )
            return

        try:
            os.startfile(folder)
        except OSError as exc:
            messagebox.showerror("Помилка", f"Не вдалося відкрити папку:\n{exc}", parent=self)

    # ---------------------------------------------------------- tooltip

    def _on_tree_motion(self, event):
        if self.tree.identify("region", event.x, event.y) != "cell":
            self._hide_tooltip()
            return

        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        if col_id != "#1" or not row_id:
            self._hide_tooltip()
            return

        if self.tree.set(row_id, "name").find("…") == -1:
            self._hide_tooltip()
            return

        if self._tooltip_row == row_id:
            return

        program = self._displayed.get(row_id)
        if not program:
            self._hide_tooltip()
            return

        self._show_tooltip(program["name"], event.x_root, event.y_root)
        self._tooltip_row = row_id

    def _show_tooltip(self, text: str, x_root: int, y_root: int):
        self._hide_tooltip()
        tip = tk.Toplevel(self)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x_root + 12}+{y_root + 18}")
        label = tk.Label(
            tip, text=text, background="#1a1a1a", foreground="#dce4ee",
            font=("Segoe UI", 10), padx=8, pady=4, relief="solid", borderwidth=1,
        )
        label.pack()
        self._tooltip_window = tip

    def _hide_tooltip(self, _event=None):
        if self._tooltip_window is not None:
            self._tooltip_window.destroy()
            self._tooltip_window = None
        self._tooltip_row = None

    def _on_destroy(self, event):
        if event.widget is self:
            self._hide_tooltip()
