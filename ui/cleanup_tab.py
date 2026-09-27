"""Вкладка «Очищення» — кеші й тимчасові файли за категоріями, пошук великих файлів."""

import threading
from tkinter import messagebox

import customtkinter as ctk

from core import admin as admin_core
from core import cleanup as cleanup_core
from core import large_files as large_files_core
from ui.admin_status import ElevateButton


class CleanupItemRow(ctk.CTkFrame):
    """Один рядок цілі очищення: чекбокс, розмір, статус/примітка."""

    def __init__(self, master, target: dict, on_toggle):
        super().__init__(master, fg_color="transparent")
        self.target = target
        self.scan_result = None
        self._on_toggle = on_toggle

        self.elevate_button = None

        self.var = ctk.BooleanVar(value=False)
        self.checkbox = ctk.CTkCheckBox(
            self, text=target["label"], variable=self.var, state="disabled",
            command=lambda: self._on_toggle(self),
        )
        self.checkbox.pack(anchor="w")

        self.status_label = ctk.CTkLabel(self, text="Сканування...", text_color="gray", font=ctk.CTkFont(size=11))
        self.status_label.pack(anchor="w", padx=(28, 0))

        if target.get("note"):
            ctk.CTkLabel(
                self, text=target["note"], text_color="#e0a52f", font=ctk.CTkFont(size=10),
                wraplength=320, justify="left",
            ).pack(anchor="w", padx=(28, 0))

    def apply_scan(self, result: dict) -> None:
        self.scan_result = result
        cleanable = (
            result["exists"]
            and not result["access_denied"]
            and not result["admin_blocked"]
            and not result["process_running"]
            and result["file_count"] > 0
        )

        if not result["exists"]:
            text, color = "Не знайдено", "gray"
        elif result["admin_blocked"]:
            text, color = "Потрібні права адміністратора", "#e0a52f"
        elif result["process_running"]:
            text, color = "Програма запущена — закрийте й оновіть сканування", "#e0a52f"
        elif result["access_denied"]:
            text, color = "Немає доступу", "#e0a52f"
        elif result["file_count"] == 0:
            text, color = "Немає що очищати", "gray"
        else:
            size_text = cleanup_core.format_size(result["size_bytes"])
            text = f"{size_text} · {result['file_count']} об'єктів"
            color = ("gray10", "gray90")

        self.status_label.configure(text=text, text_color=color)
        self.checkbox.configure(state="normal" if cleanable else "disabled")
        if not cleanable:
            self.var.set(False)

        needs_elevation = (result["admin_blocked"] or result["access_denied"]) and not admin_core.is_admin()
        if needs_elevation and self.elevate_button is None:
            self.elevate_button = ElevateButton(self)
            self.elevate_button.pack(anchor="w", padx=(28, 0), pady=(4, 0))
        elif not needs_elevation and self.elevate_button is not None:
            self.elevate_button.destroy()
            self.elevate_button = None

    def is_selected(self) -> bool:
        return bool(self.scan_result) and self.var.get()

    def size_bytes(self) -> int:
        return self.scan_result["size_bytes"] if self.scan_result else 0

    def set_busy(self) -> None:
        self.checkbox.configure(state="disabled")
        self.status_label.configure(text="Очищення...", text_color="gray")

    def show_clean_result(self, result: dict) -> None:
        if result.get("skipped_reason"):
            self.status_label.configure(text=result["skipped_reason"], text_color="#e0a52f")
            return
        freed_text = cleanup_core.format_size(result["freed_bytes"])
        text = f"Звільнено {freed_text}"
        if result["skipped_count"]:
            text += f" · пропущено {result['skipped_count']}"
        self.status_label.configure(text=text, text_color="#2fa572")


class CleanupTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.rows: dict[str, CleanupItemRow] = {}
        self._large_file_rows = []
        self._large_files_scanning = False
        self._large_files_stop_event = threading.Event()

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=0, column=0, sticky="nsew")

        ctk.CTkLabel(self.scroll, text="Очищення", font=ctk.CTkFont(size=22, weight="bold")).pack(
            padx=6, pady=(14, 4), anchor="w"
        )
        ctk.CTkLabel(
            self.scroll,
            text="Позначте категорії для очищення. Зайняті файли й запущені програми пропускаються без помилок.",
            text_color="gray",
        ).pack(padx=6, pady=(0, 14), anchor="w")

        self._build_categories()
        self._build_summary_bar()
        self._build_large_files_section()

        self.bind("<Destroy>", self._on_destroy)

        self._scan_all()

    # --------------------------------------------------------- categories

    def _build_categories(self):
        order = []
        by_category = {}
        for target in cleanup_core.get_targets():
            category = target["category"]
            if category not in by_category:
                by_category[category] = []
                order.append(category)
            by_category[category].append(target)

        for category in order:
            frame = ctk.CTkFrame(self.scroll, corner_radius=10)
            frame.pack(fill="x", padx=6, pady=6)

            ctk.CTkLabel(frame, text=category, font=ctk.CTkFont(size=14, weight="bold")).pack(
                padx=14, pady=(10, 4), anchor="w"
            )

            for target in by_category[category]:
                row = CleanupItemRow(frame, target, self._on_item_toggle)
                row.pack(fill="x", padx=14, pady=4, anchor="w")
                self.rows[target["key"]] = row

            ctk.CTkFrame(frame, fg_color="transparent", height=4).pack()

    def _on_item_toggle(self, _row: CleanupItemRow):
        self._update_summary()

    # ------------------------------------------------------------ summary

    def _build_summary_bar(self):
        bar = ctk.CTkFrame(self.scroll, corner_radius=10)
        bar.pack(fill="x", padx=6, pady=(4, 14))

        self.summary_label = ctk.CTkLabel(
            bar, text="Можна звільнити: 0 Б", font=ctk.CTkFont(size=15, weight="bold")
        )
        self.summary_label.pack(side="left", padx=14, pady=12)

        self.clean_button = ctk.CTkButton(
            bar, text="Очистити вибране", width=160, state="disabled",
            fg_color="#8b2c2c", hover_color="#a83a3a", command=self._clean_selected,
        )
        self.clean_button.pack(side="right", padx=14, pady=12)

        self.rescan_button = ctk.CTkButton(bar, text="Оновити сканування", width=160, command=self._scan_all)
        self.rescan_button.pack(side="right", padx=(0, 8), pady=12)

    def _update_summary(self):
        selected = [row for row in self.rows.values() if row.is_selected()]
        total = sum(row.size_bytes() for row in selected)
        self.summary_label.configure(text=f"Можна звільнити: {cleanup_core.format_size(total)}")
        self.clean_button.configure(state="normal" if selected else "disabled")

    # -------------------------------------------------------------- scan

    def _scan_all(self):
        self.rescan_button.configure(state="disabled")
        for row in self.rows.values():
            row.checkbox.configure(state="disabled")
            row.status_label.configure(text="Сканування...", text_color="gray")

        keys = list(self.rows.keys())

        def worker():
            def progress(key, result):
                self.after(0, self._on_scan_progress, key, result)

            cleanup_core.scan_many(keys, progress_cb=progress)
            self.after(0, self._on_scan_done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_scan_progress(self, key, result):
        if not self.winfo_exists():
            return
        row = self.rows.get(key)
        if row:
            row.apply_scan(result)
        self._update_summary()

    def _on_scan_done(self):
        if not self.winfo_exists():
            return
        self.rescan_button.configure(state="normal")

    # ------------------------------------------------------------- clean

    def _clean_selected(self):
        selected_rows = [row for row in self.rows.values() if row.is_selected()]
        if not selected_rows:
            return

        total_size = sum(row.size_bytes() for row in selected_rows)
        message = f"Очистити {len(selected_rows)} категорій ({cleanup_core.format_size(total_size)})?"
        if any(row.target["key"] == "recycle_bin" for row in selected_rows):
            message += "\n\nУвага: очищення кошика видаляє файли остаточно."

        if not messagebox.askyesno("Підтвердження", message, parent=self):
            return

        self.clean_button.configure(state="disabled")
        keys = [row.target["key"] for row in selected_rows]
        for row in selected_rows:
            row.set_busy()

        def worker():
            def progress(key, result):
                self.after(0, self._on_clean_progress, key, result)

            summary = cleanup_core.clean_many(keys, progress_cb=progress)
            self.after(0, self._on_clean_done, summary)

        threading.Thread(target=worker, daemon=True).start()

    def _on_clean_progress(self, key, result):
        if not self.winfo_exists():
            return
        row = self.rows.get(key)
        if row:
            row.show_clean_result(result)

    def _on_clean_done(self, summary: dict):
        if not self.winfo_exists():
            return
        freed_text = cleanup_core.format_size(summary["freed_bytes"])
        messagebox.showinfo(
            "Очищення завершено",
            f"Звільнено {freed_text}.\n"
            f"Видалено об'єктів: {summary['deleted_count']}\n"
            f"Пропущено: {summary['skipped_count']}",
            parent=self,
        )
        self._scan_all()

    # ------------------------------------------------------- large files

    def _build_large_files_section(self):
        section = ctk.CTkFrame(self.scroll, corner_radius=10)
        section.pack(fill="x", padx=6, pady=(0, 20))

        header = ctk.CTkFrame(section, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 4))

        ctk.CTkLabel(header, text="Великі файли", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")

        self.large_files_scan_button = ctk.CTkButton(
            header, text="Сканувати", width=120, command=self._scan_large_files
        )
        self.large_files_scan_button.pack(side="right")

        min_size_text = cleanup_core.format_size(large_files_core.DEFAULT_MIN_SIZE_BYTES)
        self.large_files_status = ctk.CTkLabel(
            section,
            text=(
                f"Пошук файлів понад {min_size_text} у Downloads, Videos, Desktop, Documents. "
                "Нічого не видаляється автоматично."
            ),
            text_color="gray",
            wraplength=760,
            justify="left",
        )
        self.large_files_status.pack(padx=14, pady=(0, 8), anchor="w")

        self.large_files_list = ctk.CTkFrame(section, fg_color="transparent")
        self.large_files_list.pack(fill="x", padx=14, pady=(0, 14))

    def _scan_large_files(self):
        if self._large_files_scanning:
            return

        self._large_files_scanning = True
        self._large_files_stop_event.clear()

        for widget in self._large_file_rows:
            widget.destroy()
        self._large_file_rows = []

        self.large_files_scan_button.configure(state="disabled")
        self.large_files_status.configure(text="Сканування...", text_color="gray")

        def on_found(entry):
            self.after(0, self._add_large_file_row, entry)

        def worker():
            results = large_files_core.scan_large_files(
                progress_cb=on_found, stop_event=self._large_files_stop_event
            )
            self.after(0, self._on_large_files_done, results)

        threading.Thread(target=worker, daemon=True).start()

    def _add_large_file_row(self, entry: dict):
        if not self.winfo_exists():
            return

        row = ctk.CTkFrame(self.large_files_list, fg_color="transparent")
        row.pack(fill="x", pady=2)
        row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(row, text=entry["path"], anchor="w").grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkLabel(
            row, text=cleanup_core.format_size(entry["size_bytes"]), text_color="gray", width=80, anchor="e"
        ).grid(row=0, column=1, sticky="e")
        ctk.CTkButton(
            row, text="Відкрити папку", width=130,
            command=lambda p=entry["path"]: large_files_core.open_containing_folder(p),
        ).grid(row=0, column=2, padx=(8, 0))

        self._large_file_rows.append(row)
        self.large_files_status.configure(text=f"Знайдено файлів: {len(self._large_file_rows)}...", text_color="gray")

    def _on_large_files_done(self, results: list):
        if not self.winfo_exists():
            return

        self._large_files_scanning = False
        self.large_files_scan_button.configure(state="normal")

        if not results:
            self.large_files_status.configure(text="Великих файлів не знайдено", text_color="gray")
        else:
            total = sum(entry["size_bytes"] for entry in results)
            self.large_files_status.configure(
                text=f"Знайдено файлів: {len(results)} · загалом {cleanup_core.format_size(total)}",
                text_color=("gray10", "gray90"),
            )

    def _on_destroy(self, event):
        if event.widget is self:
            self._large_files_stop_event.set()
