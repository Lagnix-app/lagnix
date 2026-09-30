"""Блок «Кеш програм» вкладки «Очищення»: автопошук кешу, групування за програмою,
сортування за розміром, «Вибрати все», «Показати дрібні» і «Закрити й очистити».

Сканування (core/app_cache.py) іде у фоновому потоці з прогресом; результат
кешується в ядрі до натискання «Оновити сканування».
"""

import threading

import customtkinter as ctk

from core import app_cache
from core.cleanup import format_size
from ui import bg

_WARNING = "#e0a52f"
_SUCCESS = "#2ee59d"
_ICON_SIZE = 26


def _files_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "файл"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "файли"
    return "файлів"


def _count_text(count: int) -> str:
    return f"{count:,}".replace(",", " ") + " " + _files_word(count)


class AppCacheRow(ctk.CTkFrame):
    """Одна програма: чекбокс, іконка, назва, склад кешу, розмір, кнопка дії."""

    def __init__(self, master, group: dict, on_toggle, on_clean, on_close_clean):
        super().__init__(master, fg_color="transparent")
        self.group = group
        self._on_clean = on_clean
        self._on_close_clean = on_close_clean
        self._locked = False
        self._has_icon = False
        self.has_status = False

        self.grid_columnconfigure(2, weight=1)

        self.checkbox = ctk.CTkCheckBox(self, text="", width=24, command=lambda: on_toggle(self))
        self.var = bg.WidgetBool(self.checkbox)  # без Tk-змінної: рядки створюються й знищуються динамічно
        self.checkbox.grid(row=0, column=0, padx=(0, 6), sticky="w")

        self.icon_label = ctk.CTkLabel(
            self, text="", width=_ICON_SIZE + 2, height=_ICON_SIZE + 2, corner_radius=6,
            fg_color=("gray80", "gray25"), font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.icon_label.grid(row=0, column=1, padx=(0, 10))

        text = ctk.CTkFrame(self, fg_color="transparent")
        text.grid(row=0, column=2, sticky="ew")
        self.name_label = ctk.CTkLabel(text, text="", font=ctk.CTkFont(size=13, weight="bold"), anchor="w")
        self.name_label.pack(anchor="w")
        self.meta_label = ctk.CTkLabel(text, text="", text_color="gray", font=ctk.CTkFont(size=11),
                                       anchor="w", justify="left", wraplength=520)
        self.meta_label.pack(anchor="w")
        self.note_label = ctk.CTkLabel(text, text="", text_color=_WARNING, font=ctk.CTkFont(size=10),
                                       anchor="w", justify="left", wraplength=520)
        self.status_label = ctk.CTkLabel(text, text="", font=ctk.CTkFont(size=11), anchor="w")

        self.size_label = ctk.CTkLabel(self, text="", width=84, anchor="e",
                                       font=ctk.CTkFont(size=13, weight="bold"))
        self.size_label.grid(row=0, column=3, padx=(8, 8))

        self.action_button = ctk.CTkButton(self, text="Очистити", width=150, height=26,
                                           font=ctk.CTkFont(size=11), command=self._on_action)
        self.action_button.grid(row=0, column=4, sticky="e")

        self.update_group(group)

    # ------------------------------------------------------------- state

    def update_group(self, group: dict) -> None:
        self.group = group
        self.name_label.configure(text=group["name"])
        if not self._has_icon:
            self.icon_label.configure(text=(group["name"][:1] or "?").upper())

        labels = []
        for folder in group["folders"]:
            if folder["label"] not in labels:
                labels.append(folder["label"])
        meta = f"{_count_text(group['file_count'])} · {', '.join(labels)}"
        if group["running"]:
            meta = "Програма запущена · " + meta
        self.meta_label.configure(text=meta, text_color=_WARNING if group["running"] else "gray")

        notes = sorted({f["note"] for f in group["folders"] if f.get("note")})
        if notes:
            self.note_label.configure(text="\n".join(notes))
            self.note_label.pack(anchor="w")
        else:
            self.note_label.pack_forget()

        self.size_label.configure(text=format_size(group["size_bytes"]))
        if group["running"]:
            self.var.set(False)
            self.action_button.configure(text="Закрити й очистити", fg_color="#9a6a12", hover_color=_WARNING)
        else:
            self.action_button.configure(text="Очистити", fg_color=("#3a7ebf", "#1f538d"),
                                         hover_color=("#325882", "#14375e"))
        self._apply_lock()

    def set_icon(self, image) -> None:
        self._has_icon = True
        self.icon_label.configure(image=image, text="", fg_color="transparent")

    def is_cleanable(self) -> bool:
        return not self.group["running"] and self.group["file_count"] > 0

    def is_selected(self) -> bool:
        return self.is_cleanable() and self.var.get()

    def lock(self, locked: bool) -> None:
        self._locked = locked
        self._apply_lock()

    def _apply_lock(self) -> None:
        checkbox_ok = not self._locked and self.is_cleanable()
        self.checkbox.configure(state="normal" if checkbox_ok else "disabled")
        self.action_button.configure(state="disabled" if self._locked or not self.group["file_count"] else "normal")

    def set_status(self, text: str, color) -> None:
        self.has_status = bool(text)
        if text:
            self.status_label.configure(text=text, text_color=color)
            self.status_label.pack(anchor="w")
        else:
            self.status_label.pack_forget()

    def _on_action(self) -> None:
        if self.group["running"]:
            self._on_close_clean(self)
        else:
            self._on_clean(self)


class AppCacheSection(ctk.CTkFrame):
    """Картка «Кеш програм». on_change() — змінився вибір/стан (для підсумку вкладки)."""

    def __init__(self, master, title: str, on_change, on_clean, on_close_clean):
        super().__init__(master, corner_radius=10)
        self._on_change = on_change
        self._on_clean = on_clean
        self._on_close_clean = on_close_clean
        self.rows: dict[str, AppCacheRow] = {}
        self._icons: dict[str, object] = {}
        self._icon_requested: set[str] = set()
        self._show_small = False
        self._locked = False
        self.scanning = False
        self._stop_event = threading.Event()
        self._scan_generation = 0

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(header, text=title, font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")

        self.select_all_var = ctk.BooleanVar(value=False)
        self.select_all_checkbox = ctk.CTkCheckBox(
            header, text="Вибрати все", variable=self.select_all_var, font=ctk.CTkFont(size=12),
            command=lambda: self.select_all(self.select_all_var.get()),
        )
        self.select_all_checkbox.pack(side="right")

        self.total_label = ctk.CTkLabel(header, text="", font=ctk.CTkFont(size=14, weight="bold"))
        self.total_label.pack(side="right", padx=(0, 18))

        ctk.CTkLabel(
            self,
            text=("Автопошук кешу в AppData, Microsoft Store і теках лаунчерів. Паролі, cookies, сесії, "
                  "профілі, збереження й налаштування не зачіпаються."),
            text_color="gray", font=ctk.CTkFont(size=11), wraplength=760, justify="left",
        ).pack(padx=14, pady=(0, 6), anchor="w")

        self.progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.progress_bar = ctk.CTkProgressBar(self.progress_frame, height=8)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", pady=(2, 2))
        self.progress_label = ctk.CTkLabel(self.progress_frame, text="", text_color="gray",
                                           font=ctk.CTkFont(size=11), anchor="w")
        self.progress_label.pack(anchor="w")

        self.list_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.list_frame.pack(fill="x", padx=14)

        self.empty_label = ctk.CTkLabel(self, text="Кешу програм не знайдено", text_color="gray")

        self.small_button = ctk.CTkButton(
            self, text="", height=26, fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"), font=ctk.CTkFont(size=11), command=self._toggle_small,
        )
        self.small_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.bottom_pad = ctk.CTkFrame(self, fg_color="transparent", height=8)
        self.bottom_pad.pack()

        self.bind("<Destroy>", self._on_destroy)
        bg.ensure_pump(self)  # доставка результатів фонових потоків (ui/bg.py), навіть без MainWindow

    # ------------------------------------------------------------ scan

    def scan(self, force: bool, on_done=None) -> None:
        """force=False — узяти кешований результат (і лише оновити стан «запущена»)."""
        if self.scanning:
            return
        cached = None if force else app_cache.get_cached()
        self.scanning = True
        self._scan_generation += 1
        generation = self._scan_generation
        self.lock(True)

        if cached is not None:
            self.apply_groups(cached)
            job = app_cache.refresh_running
        else:
            self.progress_bar.set(0)
            self.progress_label.configure(text="Сканування…")
            self.progress_frame.pack(fill="x", padx=14, pady=(0, 6), before=self.list_frame)
            self.total_label.configure(text="")
            job = self._full_scan_job(generation)

        def worker():
            try:
                groups = job()
            except Exception:
                groups = None
            self._post(self._on_scan_done, generation, groups, on_done)

        threading.Thread(target=worker, daemon=True).start()

    def _full_scan_job(self, generation):
        state = {"last": 0.0}

        def progress(fraction, text):
            # Не частіше ніж раз на ~1% — щоб не засипати чергу подій Tk.
            if fraction - state["last"] >= 0.01 or fraction >= 1.0:
                state["last"] = fraction
                self._post(self._on_progress, generation, fraction, text)

        return lambda: app_cache.scan(progress_cb=progress, stop_event=self._stop_event)

    def refresh(self, keys: list[str], on_done=None) -> None:
        """Після очищення — перерахувати лише ці групи (решта береться з кешу)."""
        self.scanning = True
        self._scan_generation += 1
        generation = self._scan_generation

        def worker():
            try:
                groups = app_cache.rescan_groups(keys)
            except Exception:
                groups = None
            self._post(self._on_scan_done, generation, groups, on_done, keep_status=True)

        threading.Thread(target=worker, daemon=True).start()

    def _post(self, fn, *args, **kwargs) -> None:
        bg.ui_call(self, lambda: fn(*args, **kwargs))

    def _on_progress(self, generation, fraction, text) -> None:
        if generation != self._scan_generation or not self.winfo_exists():
            return
        self.progress_bar.set(fraction)
        self.progress_label.configure(text=f"Сканування {int(fraction * 100)}% · {text}")

    def _on_scan_done(self, generation, groups, on_done, keep_status=False) -> None:
        if generation != self._scan_generation or not self.winfo_exists():
            return
        self.scanning = False
        self.progress_frame.pack_forget()
        if groups is not None:
            self.apply_groups(groups, keep_status=keep_status)
        self.lock(False)
        if on_done:
            on_done()

    # ----------------------------------------------------------- render

    def apply_groups(self, groups: list[dict], keep_status: bool = False) -> None:
        keys = {g["key"] for g in groups}
        for key in list(self.rows):
            if key not in keys:
                if keep_status and self.rows[key].has_status:
                    # Очищено повністю — лишаємо рядок з результатом до наступного сканування.
                    row = self.rows[key]
                    row.group = dict(row.group, size_bytes=0, file_count=0, running=False)
                    row.update_group(row.group)
                    continue
                self.rows.pop(key).destroy()

        for group in groups:
            row = self.rows.get(group["key"])
            if row is None:
                row = AppCacheRow(self, group, self._row_toggled, self._on_clean, self._on_close_clean)
                self.rows[group["key"]] = row
            else:
                row.update_group(group)
                if not keep_status:
                    row.set_status("", None)
            if group["key"] in self._icons:
                row.set_icon(self._icons[group["key"]])

        self._layout()
        self._load_icons(groups)
        self._on_change()

    def _layout(self) -> None:
        ordered = sorted(self.rows.values(), key=lambda r: (-r.group["size_bytes"], r.group["name"].lower()))
        big = [r for r in ordered if not app_cache.is_small(r.group)]
        small = [r for r in ordered if app_cache.is_small(r.group)]

        for row in ordered:
            row.pack_forget()
        for row in big:
            row.pack(in_=self.list_frame, fill="x", pady=4)
        for row in small:
            row.pack(in_=self.small_frame, fill="x", pady=4)

        for widget in (self.empty_label, self.small_button, self.small_frame):
            widget.pack_forget()
        if not ordered:
            self.empty_label.pack(padx=14, pady=(0, 6), anchor="w", before=self.bottom_pad)
        if small:
            small_size = sum(r.group["size_bytes"] for r in small)
            arrow = "▴ Сховати дрібні" if self._show_small else "▾ Показати дрібні"
            self.small_button.configure(text=f"{arrow} (менше 10 МБ: {len(small)} · {format_size(small_size)})")
            self.small_button.pack(padx=14, pady=(4, 2), anchor="w", before=self.bottom_pad)
            if self._show_small:
                self.small_frame.pack(fill="x", padx=14, before=self.bottom_pad)

        total = sum(r.group["size_bytes"] for r in ordered)
        count = sum(1 for r in ordered if r.group["file_count"])
        self.total_label.configure(text=f"Усього {format_size(total)} · {count} програм" if count else "")

    def _toggle_small(self) -> None:
        self._show_small = not self._show_small
        self._layout()

    def _load_icons(self, groups: list[dict]) -> None:
        pending = [(g["key"], g["exe"]) for g in groups
                   if g.get("exe") and g["key"] not in self._icon_requested]
        if not pending:
            return
        self._icon_requested.update(key for key, _exe in pending)

        def worker():
            from core.app_icons import extract_icon
            for key, exe in pending:
                try:
                    image = extract_icon(exe)
                except Exception:
                    image = None
                if image is not None:
                    self._post(self._set_icon, key, image)

        threading.Thread(target=worker, daemon=True).start()

    def _set_icon(self, key, pil_image) -> None:
        if not self.winfo_exists():
            return
        image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(_ICON_SIZE, _ICON_SIZE))
        self._icons[key] = image
        row = self.rows.get(key)
        if row:
            row.set_icon(image)

    # -------------------------------------------------------- selection

    def _row_toggled(self, _row) -> None:
        self._sync_select_all()
        self._on_change()

    def _sync_select_all(self) -> None:
        cleanable = [r for r in self.rows.values() if r.is_cleanable()]
        self.select_all_var.set(bool(cleanable) and all(r.var.get() for r in cleanable))

    def select_all(self, value: bool) -> None:
        for row in self.rows.values():
            row.var.set(value and row.is_cleanable())
        self._sync_select_all()
        self._on_change()

    def selected_rows(self) -> list[AppCacheRow]:
        return [r for r in self.rows.values() if r.is_selected()]

    def selected_size(self) -> int:
        return sum(r.group["size_bytes"] for r in self.selected_rows())

    def lock(self, locked: bool) -> None:
        self._locked = locked
        for row in self.rows.values():
            row.lock(locked)
        self.select_all_checkbox.configure(state="disabled" if locked else "normal")

    def set_busy(self, key: str) -> None:
        row = self.rows.get(key)
        if row:
            row.set_status("Очищення…", "gray")

    def show_result(self, key: str, result: dict) -> None:
        row = self.rows.get(key)
        if not row:
            return
        if result.get("skipped_reason"):
            row.set_status(result["skipped_reason"], _WARNING)
            return
        text = f"Звільнено {format_size(result['freed_bytes'])}"
        if result["skipped_count"]:
            text += f" · пропущено {result['skipped_count']} (зайняті)"
        row.set_status(text, _SUCCESS)

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            self._stop_event.set()
