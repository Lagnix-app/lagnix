"""Вкладка «Очищення» — кеші й тимчасові файли за категоріями, пошук великих файлів."""

import threading
from tkinter import messagebox

import customtkinter as ctk

from core import app_cache as app_cache_core
from core import cleanup as cleanup_core
from core import large_files as large_files_core
from ui.cleanup_app_cache import AppCacheSection
from ui.widgets.cleaner_bot_dialog import CleanerBotDialog

RECOMMENDED_CATEGORIES = {
    cleanup_core.CAT_TEMP,
    cleanup_core.CAT_BROWSERS,
    cleanup_core.CAT_THUMBNAILS,
}  # + усі незапущені програми з блоку «Кеш програм»


class CleanupItemRow(ctk.CTkFrame):
    """Один рядок цілі очищення: чекбокс, розмір, статус/примітка, кнопка «Очистити»."""

    def __init__(self, master, target: dict, on_toggle, on_clean_one):
        super().__init__(master, fg_color="transparent")
        self.target = target
        self.scan_result = None
        self._cleanable = False
        self._on_toggle = on_toggle
        self._on_clean_one = on_clean_one

        self.grid_columnconfigure(0, weight=1)

        text_frame = ctk.CTkFrame(self, fg_color="transparent")
        text_frame.grid(row=0, column=0, sticky="w")
        self._text_frame = text_frame

        self.var = ctk.BooleanVar(value=False)
        self.checkbox = ctk.CTkCheckBox(
            text_frame, text=target["label"], variable=self.var, state="disabled",
            command=lambda: self._on_toggle(self),
        )
        self.checkbox.pack(anchor="w")

        self.status_label = ctk.CTkLabel(
            text_frame, text="Сканування...", text_color="gray", font=ctk.CTkFont(size=11)
        )
        self.status_label.pack(anchor="w", padx=(28, 0))

        if target.get("note"):
            ctk.CTkLabel(
                text_frame, text=target["note"], text_color="#e0a52f", font=ctk.CTkFont(size=10),
                wraplength=320, justify="left",
            ).pack(anchor="w", padx=(28, 0))

        self.clean_one_button = ctk.CTkButton(
            self, text="Очистити", width=88, height=26, font=ctk.CTkFont(size=11),
            state="disabled", command=lambda: self._on_clean_one(self),
        )
        self.clean_one_button.grid(row=0, column=1, padx=(8, 0), sticky="e")

    def apply_scan(self, result: dict) -> None:
        self.scan_result = result
        cleanable = (
            result["exists"]
            and not result["access_denied"]
            and not result["admin_blocked"]
            and not result["process_running"]
            and result["file_count"] > 0
        )
        self._cleanable = cleanable

        if not result["exists"]:
            text, color = "Не знайдено", "gray"
        elif result["admin_blocked"]:
            text, color = "Пропущено", "gray"
        elif result["process_running"]:
            text, color = "Програма запущена — закрийте й оновіть сканування", "#e0a52f"
        elif result["access_denied"]:
            text, color = "Пропущено — папку захищено системою", "gray"
        elif result["file_count"] == 0:
            text, color = "Немає що очищати", "gray"
        else:
            size_text = cleanup_core.format_size(result["size_bytes"])
            text = f"{size_text} · {result['file_count']} об'єктів"
            color = ("gray10", "gray90")

        self.status_label.configure(text=text, text_color=color)
        self.checkbox.configure(state="normal" if cleanable else "disabled")
        self.clean_one_button.configure(state="normal" if cleanable else "disabled")
        if not cleanable:
            self.var.set(False)

    def is_selected(self) -> bool:
        return bool(self.scan_result) and self.var.get()

    def is_cleanable(self) -> bool:
        return self._cleanable

    def size_bytes(self) -> int:
        return self.scan_result["size_bytes"] if self.scan_result else 0

    def lock_controls(self) -> None:
        self.checkbox.configure(state="disabled")
        self.clean_one_button.configure(state="disabled")

    def set_busy(self) -> None:
        self.lock_controls()
        self.status_label.configure(text="Очищення...", text_color="gray")

    def show_clean_result(self, result: dict) -> None:
        if result.get("skipped_reason"):
            self.status_label.configure(text=result["skipped_reason"], text_color="#e0a52f")
            return
        freed_text = cleanup_core.format_size(result["freed_bytes"])
        text = f"Звільнено {freed_text}"
        if result["skipped_count"]:
            text += f" · пропущено {result['skipped_count']}"
        self.status_label.configure(text=text, text_color="#2ee59d")


class CleanupTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.rows: dict[str, CleanupItemRow] = {}
        self._large_file_rows = []
        self._large_files_scanning = False
        self._large_files_stop_event = threading.Event()
        self._cleaning_in_progress = False
        self._static_scanning = False
        self._clean_static_keys: list[str] = []
        self._clean_app_keys: list[str] = []

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

        self._clean_dialog = None

        self._build_top_actions()
        self._build_categories()
        self._build_large_files_section()
        self._build_summary_bar()

        self.bind("<Destroy>", self._on_destroy)

        # Через after(0, ...), а не напряму: вкладки створюються ще до
        # MainWindow.mainloop(), і фоновий потік сканування міг би
        # викликати self.after() ще до реального старту mainloop
        # (Python 3.13+ кидає на це непіймане RuntimeError, і потік мовчки
        # гине, залишаючи категорії без даних сканування).
        self.after(0, lambda: self._scan_all(force=False))

    # ----------------------------------------------------------- top actions

    def _build_top_actions(self):
        bar = ctk.CTkFrame(self.scroll, fg_color="transparent")
        bar.pack(fill="x", padx=6, pady=(0, 10))

        self.select_recommended_button = ctk.CTkButton(
            bar, text="Вибрати рекомендоване", width=200, command=self._select_recommended
        )
        self.select_recommended_button.pack(side="left")

        self.select_none_button = ctk.CTkButton(
            bar, text="Зняти все", width=110, fg_color="transparent", border_width=1,
            command=self._select_none,
        )
        self.select_none_button.pack(side="left", padx=(8, 0))

    def _select_recommended(self):
        for row in self.rows.values():
            in_recommended = row.target["category"] in RECOMMENDED_CATEGORIES
            row.var.set(in_recommended and row.is_cleanable())
        self.app_section.select_all(True)
        self._update_summary()

    def _select_none(self):
        for row in self.rows.values():
            row.var.set(False)
        self.app_section.select_all(False)
        self._update_summary()

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

        # «Кеш програм» — окремий блок з автопошуком одразу після браузерів.
        if cleanup_core.CAT_BROWSERS in order:
            order.insert(order.index(cleanup_core.CAT_BROWSERS) + 1, cleanup_core.CAT_APPS)
        else:
            order.append(cleanup_core.CAT_APPS)

        for category in order:
            if category == cleanup_core.CAT_APPS:
                self.app_section = AppCacheSection(
                    self.scroll, category, on_change=self._update_summary,
                    on_clean=self._clean_one_app, on_close_clean=self._close_and_clean_app,
                )
                self.app_section.pack(fill="x", padx=6, pady=6)
                continue

            frame = ctk.CTkFrame(self.scroll, corner_radius=10)
            frame.pack(fill="x", padx=6, pady=6)

            ctk.CTkLabel(frame, text=category, font=ctk.CTkFont(size=14, weight="bold")).pack(
                padx=14, pady=(10, 4), anchor="w"
            )

            for target in by_category[category]:
                row = CleanupItemRow(frame, target, self._on_item_toggle, self._clean_one)
                row.pack(fill="x", padx=14, pady=4, anchor="w")
                self.rows[target["key"]] = row

            ctk.CTkFrame(frame, fg_color="transparent", height=4).pack()

    def _on_item_toggle(self, _row: CleanupItemRow):
        self._update_summary()

    # ------------------------------------------------------------ summary

    def _build_summary_bar(self):
        # Поза self.scroll і закріплена в окремому рядку grid, щоб завжди лишатись видимою.
        bar = ctk.CTkFrame(self, corner_radius=10)
        bar.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 10))

        self.summary_label = ctk.CTkLabel(
            bar, text="Можна звільнити: 0 Б", font=ctk.CTkFont(size=15, weight="bold")
        )
        self.summary_label.pack(side="left", padx=14, pady=12)

        self.clean_button = ctk.CTkButton(
            bar, text="Очистити вибране", width=160, state="disabled",
            fg_color="#a8283f", hover_color="#ff5c7a", command=self._clean_selected,
        )
        self.clean_button.pack(side="right", padx=14, pady=12)

        self.rescan_button = ctk.CTkButton(bar, text="Оновити сканування", width=160, command=self._scan_all)
        self.rescan_button.pack(side="right", padx=(0, 8), pady=12)

    def _update_summary(self):
        if not hasattr(self, "summary_label"):
            return  # блок «Кеш програм» створюється раніше за панель підсумку
        selected = [row for row in self.rows.values() if row.is_selected()]
        app_selected = self.app_section.selected_rows()
        total = sum(row.size_bytes() for row in selected) + self.app_section.selected_size()
        self.summary_label.configure(text=f"Можна звільнити: {cleanup_core.format_size(total)}")
        if not self._cleaning_in_progress:
            self.clean_button.configure(state="normal" if selected or app_selected else "disabled")

    # -------------------------------------------------------------- scan

    def _scan_all(self, force: bool = True):
        """force=False — кеш програм береться з попереднього сканування (якщо воно було)."""
        self._set_scan_controls(False)
        self._scan_static()
        self.app_section.scan(force=force, on_done=self._on_scan_done)

    def _scan_static(self, keys: list[str] | None = None):
        keys = list(self.rows.keys()) if keys is None else keys
        self._static_scanning = True
        self._set_scan_controls(False)
        for key in keys:
            self.rows[key].lock_controls()
            self.rows[key].status_label.configure(text="Сканування...", text_color="gray")

        def worker():
            def progress(key, result):
                self.after(0, self._on_scan_progress, key, result)

            cleanup_core.scan_many(keys, progress_cb=progress)
            self.after(0, self._on_static_scan_done)

        threading.Thread(target=worker, daemon=True).start()

    def _set_scan_controls(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for button in (self.rescan_button, self.select_recommended_button, self.select_none_button):
            button.configure(state=state)

    def _on_scan_progress(self, key, result):
        if not self.winfo_exists():
            return
        row = self.rows.get(key)
        if row:
            row.apply_scan(result)
        self._update_summary()

    def _on_static_scan_done(self):
        if not self.winfo_exists():
            return
        self._static_scanning = False
        self._on_scan_done()

    def _on_scan_done(self):
        if not self.winfo_exists():
            return
        if not (self._static_scanning or self.app_section.scanning or self._cleaning_in_progress):
            self._set_scan_controls(True)
        self._update_summary()

    # ------------------------------------------------------------- clean

    def _clean_selected(self):
        if self._cleaning_in_progress:
            return
        selected_rows = [row for row in self.rows.values() if row.is_selected()]
        app_rows = self.app_section.selected_rows()
        if not selected_rows and not app_rows:
            return

        total_size = sum(row.size_bytes() for row in selected_rows) + self.app_section.selected_size()
        count = len(selected_rows) + len(app_rows)
        message = f"Очистити {count} пунктів ({cleanup_core.format_size(total_size)})?"
        if any(row.target["key"] == "recycle_bin" for row in selected_rows):
            message += "\n\nУвага: очищення кошика видаляє файли остаточно."

        if not messagebox.askyesno("Підтвердження", message, parent=self):
            return

        self._start_clean([row.target["key"] for row in selected_rows], [row.group["key"] for row in app_rows])

    def _clean_one(self, row: CleanupItemRow):
        if self._cleaning_in_progress or not row.is_cleanable():
            return

        size_text = cleanup_core.format_size(row.size_bytes())
        message = f"Очистити «{row.target['label']}» ({size_text})?"
        if row.target["key"] == "recycle_bin":
            message += "\n\nУвага: очищення кошика видаляє файли остаточно."

        if not messagebox.askyesno("Підтвердження", message, parent=self):
            return

        self._start_clean([row.target["key"]], [])

    def _clean_one_app(self, row):
        if self._cleaning_in_progress or not row.is_cleanable():
            return
        group = row.group
        message = f"Очистити кеш «{group['name']}» ({cleanup_core.format_size(group['size_bytes'])})?"
        if not messagebox.askyesno("Підтвердження", message, parent=self):
            return
        self._start_clean([], [group["key"]])

    def _close_and_clean_app(self, row):
        if self._cleaning_in_progress:
            return
        group = row.group
        message = (
            f"«{group['name']}» зараз запущена. Закрити її й очистити кеш "
            f"({cleanup_core.format_size(group['size_bytes'])})?\n\n"
            "Незбережені дані в цій програмі можуть бути втрачені. Після очищення "
            "PulseFPS запропонує запустити її знову."
        )
        if not messagebox.askyesno("Закрити й очистити", message, icon="warning", parent=self):
            return
        self._start_clean([], [group["key"]], close_first=True)

    def _start_clean(self, keys: list[str], app_keys: list[str], close_first: bool = False):
        self._cleaning_in_progress = True
        self.clean_button.configure(state="disabled")
        self._set_scan_controls(False)
        self.app_section.lock(True)

        active_rows = [self.rows[key] for key in keys if key in self.rows]
        for row in self.rows.values():
            row.lock_controls()
        for row in active_rows:
            row.set_busy()
        for key in app_keys:
            self.app_section.set_busy(key)

        self._clean_total_count = len(keys) + len(app_keys)
        self._clean_done_count = 0
        self._clean_freed_so_far = 0
        self._clean_key_labels = {row.target["key"]: row.target["label"] for row in active_rows}
        self._clean_key_labels.update({key: app_cache_core.group_name(key) for key in app_keys})
        self._clean_static_keys = keys
        self._clean_app_keys = app_keys

        first = (keys or app_keys)[0]
        verb = "Закриваю" if close_first else "Очищаю"
        self._clean_dialog = CleanerBotDialog(self.winfo_toplevel(), title="Очищення")
        self._clean_dialog.start(f"{verb}: {self._clean_key_labels[first]}…")

        def worker():
            def on_item_start(key):
                self.after(0, self._on_clean_item_start, key)

            def progress(key, result):
                self.after(0, self._on_clean_progress, key, result)

            summary = cleanup_core.clean_many(keys, progress_cb=progress, start_cb=on_item_start)
            relaunch = None
            for key in app_keys:
                if close_first:
                    closed = app_cache_core.close_group(key)
                    if not closed["ok"]:
                        progress(key, {"key": key, "freed_bytes": 0, "deleted_count": 0, "skipped_count": 0,
                                       "skipped_reason": closed["message"]})
                        summary["skipped_count"] += 1
                        continue
                    relaunch = closed["relaunch"]
                on_item_start(key)
                result = app_cache_core.clean_group(key)
                summary["freed_bytes"] += result["freed_bytes"]
                summary["deleted_count"] += result["deleted_count"]
                summary["skipped_count"] += result["skipped_count"] + (1 if result.get("skipped_reason") else 0)
                progress(key, result)
            summary["relaunch"] = relaunch
            self.after(0, self._on_clean_done, summary)

        threading.Thread(target=worker, daemon=True).start()

    def _on_clean_item_start(self, key):
        if not self.winfo_exists() or self._clean_dialog is None:
            return
        label = self._clean_key_labels.get(key, key)
        self._clean_dialog.set_status(f"Очищаю: {label}…")

    def _on_clean_progress(self, key, result):
        if not self.winfo_exists():
            return
        row = self.rows.get(key)
        if row:
            row.show_clean_result(result)
        elif key in self._clean_app_keys:
            self.app_section.show_result(key, result)

        self._clean_done_count += 1
        self._clean_freed_so_far += result.get("freed_bytes", 0)
        if self._clean_dialog is not None:
            freed_text = cleanup_core.format_size(self._clean_freed_so_far)
            progress_fraction = self._clean_done_count / self._clean_total_count
            self._clean_dialog.set_progress(progress_fraction, freed_text)

    def _on_clean_done(self, summary: dict):
        if not self.winfo_exists():
            return
        freed_text = cleanup_core.format_size(summary["freed_bytes"])
        text = f"Готово! Звільнено {freed_text}, пропущено {summary['skipped_count']} файлів"
        dialog, self._clean_dialog = self._clean_dialog, None
        if dialog is not None:
            dialog.finish(text, success=True)
        self._cleaning_in_progress = False

        # Кеш програм повністю не пересканується — лише очищені групи; решта рядків
        # просто розблоковується з попереднім результатом.
        for key, row in self.rows.items():
            if key not in self._clean_static_keys and row.scan_result:
                row.apply_scan(row.scan_result)
        if not self._clean_app_keys:
            self.app_section.lock(False)
        if self._clean_static_keys:
            self._scan_static(self._clean_static_keys)
        if self._clean_app_keys:
            self._set_scan_controls(False)
            self.app_section.refresh(self._clean_app_keys, on_done=self._on_scan_done)

        relaunch = summary.get("relaunch")
        if relaunch and self._clean_app_keys:
            name = app_cache_core.group_name(self._clean_app_keys[0])
            self.after(900, lambda: self._offer_relaunch(dialog, name, relaunch, freed_text))

    def _offer_relaunch(self, dialog, name: str, target, freed_text: str):
        if not self.winfo_exists():
            return
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()  # інакше модальне вікно робота перехоплює фокус у messagebox
        message = f"Кеш «{name}» очищено (звільнено {freed_text}).\n\nЗапустити {name} знову?"
        if messagebox.askyesno("Запустити знову?", message, parent=self):
            if not app_cache_core.relaunch(target):
                messagebox.showwarning("Запуск", f"Не вдалося запустити {name}. Відкрийте програму вручну.",
                                       parent=self)

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
