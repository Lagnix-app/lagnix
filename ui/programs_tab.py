"""Вкладка «Програми» — встановлені програми з реєстру у вигляді карток: діаграма
зайнятого місця за категоріями, пошук і фільтри, сортування, іконки програм,
видалення (для ігор Steam — через Steam), відкриття папки та пакетне видалення
вибраних програм з роботом-прибиральником.
"""

import os
import threading
import tkinter as tk
from datetime import date
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

from core import installed_programs as programs_core
from core.cleanup import format_size
from ui import bg, theme
from ui.widgets import aa
from ui.widgets.cleaner_bot_dialog import CleanerBotDialog
from ui.widgets.program_list import CATEGORIES, VirtualList
from core.i18n import TDict, t

BIG_PROGRAM_BYTES = 10 * 1024 ** 3
OLD_PROGRAM_DAYS = 365
SEARCH_DEBOUNCE_MS = 150
LOAD_TIMEOUT_S = 60
SUMMARY_DEBOUNCE_MS = 250

SORT_LABELS = TDict({
    "size": "programs.sort.size",
    "name": "programs.sort.name",
    "date": "programs.sort.date",
})
# напрямок за замовчуванням при першому виборі поля: розмір/дата — від більших/новіших
SORT_DEFAULT_DESC = {"size": True, "name": False, "date": True}


# ------------------------------------------------------------------- діаграма місця

class UsageBar(ctk.CTkFrame):
    """Смужка зайнятого місця за категоріями з легендою й розмірами (Pillow, 4x)."""

    BAR_DP = 12

    def __init__(self, master):
        super().__init__(master, corner_radius=14)
        self._scale = self._get_widget_scaling()
        self._sizes = {key: 0 for key, _l, _c in CATEGORIES}
        self._photo = None
        self._job = None
        self._size = (0, 0)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 0))
        ctk.CTkLabel(header, text=t("programs.space_by_category"), font=theme.font_header()).pack(side="left")
        self.total_label = ctk.CTkLabel(header, text="", font=theme.font_small(), text_color=theme.TEXT_DIM)
        self.total_label.pack(side="right")

        self.canvas = tk.Canvas(
            self, height=round(self.BAR_DP * self._scale), bg=theme.BG_PANEL, highlightthickness=0,
        )
        self.canvas.pack(fill="x", padx=16, pady=(8, 8))
        self._image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.canvas.bind("<Configure>", lambda _e: self._schedule())
        self.canvas.bind("<Map>", lambda _e: self._schedule())

        legend = ctk.CTkFrame(self, fg_color="transparent")
        legend.pack(fill="x", padx=16, pady=(0, 12))
        self._legend_labels = {}
        for key, label, color in CATEGORIES:
            item = ctk.CTkFrame(legend, fg_color="transparent")
            item.pack(side="left", padx=(0, 20))
            tk.Label(
                item, image=aa.dot_image(color, 10, theme.BG_PANEL, self._scale),
                bg=theme.BG_PANEL, bd=0, highlightthickness=0,
            ).pack(side="left", padx=(0, 6))
            ctk.CTkLabel(item, text=t(label), font=theme.font_small()).pack(side="left")
            size_label = ctk.CTkLabel(item, text="—", font=theme.font_small(), text_color=theme.TEXT_DIM)
            size_label.pack(side="left", padx=(6, 0))
            self._legend_labels[key] = size_label

    def set_data(self, sizes: dict[str, int], approx: bool) -> None:
        self._sizes = sizes
        prefix = "~" if approx else ""
        total = sum(sizes.values())
        self.total_label.configure(text=t("programs.total", prefix=prefix, total=format_size(total)) if total else "")
        for key, label in self._legend_labels.items():
            label.configure(text=f"{prefix}{format_size(sizes.get(key, 0))}" if sizes.get(key) else "—")
        self._schedule()

    def _schedule(self) -> None:
        if self._job is None:
            self._job = self.after(30, self._render)

    def _render(self) -> None:
        self._job = None
        if not self.canvas.winfo_ismapped():
            return
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w <= 8 or h <= 4:
            return
        K = aa.SS
        W, H = w * K, h * K
        bars = Image.new("RGB", (W, H), aa.rgb(theme.BORDER))
        d = ImageDraw.Draw(bars)
        total = sum(self._sizes.values())
        if total:
            gap = 2 * K
            done = 0
            x = 0.0
            active = [(key, color) for key, _l, color in CATEGORIES if self._sizes.get(key)]
            for i, (key, color) in enumerate(active):
                done += self._sizes[key]
                x1 = W if i == len(active) - 1 else W * done / total
                d.rectangle((round(x), 0, round(x1), H), fill=aa.rgb(color))
                if i < len(active) - 1:
                    d.rectangle((round(x1) - gap // 2, 0, round(x1) + gap // 2, H), fill=aa.rgb(theme.BG_PANEL))
                x = x1
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, W - 1, H - 1), radius=H / 2, fill=255)
        layer = Image.new("RGB", (W, H), aa.rgb(theme.BG_PANEL))
        layer.paste(bars, (0, 0), mask)
        img = aa.downscale(layer, (w, h))
        if self._photo is not None and self._size == (w, h):
            self._photo.paste(img)  # той самий PhotoImage — без нового об'єкта Tk
        else:
            self._photo = ImageTk.PhotoImage(img)
            self._size = (w, h)
            self.canvas.itemconfigure(self._image_item, image=self._photo)


# --------------------------------------------------------------------------- вкладка

class ProgramsTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.all_programs: list[dict] = []
        self._by_key: dict[str, dict] = {}
        self._sort_key = "size"
        self._sort_desc = True
        self._category_filter = "all"  # all / game / app
        self._filter_big = False
        self._filter_old = False
        self._selected: set[str] = set()
        self._busy = False
        self._abort = threading.Event()
        self._loaded = False
        self._search_job = None
        self._summary_job = None
        self._scale = self._get_widget_scaling()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._build_header()
        self._build_controls()
        self._build_list()
        self._build_footer()

        # Результати фонових потоків ідуть через ui/bg.py (черга + таймер у потоці UI),
        # тож не губляться, навіть якщо потік стартував раніше за mainloop.
        bg.ensure_pump(self)
        self._load_task = None
        self._abort_sizes = threading.Event()
        self.bind("<Destroy>", lambda e: self._abort_sizes.set() if e.widget is self else None, add="+")
        self.after(0, self._load)

    # ------------------------------------------------------------------ UI

    def _build_header(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        ctk.CTkLabel(head, text=t("tabs.programs"), font=theme.font_title()).pack(anchor="w")
        self.status_label = ctk.CTkLabel(
            head, text=t("programs.loading_list"), text_color=theme.TEXT_DIM, font=theme.font_body(),
        )
        self.status_label.pack(anchor="w", pady=(2, 0))
        self.retry_button = ctk.CTkButton(
            head, text=t("common.retry"), width=110, height=30, corner_radius=8, font=theme.font_small(),
            fg_color=theme.BG_PANEL_LIGHT, hover_color=theme.BORDER, text_color=theme.TEXT_MAIN, command=self._load,
        )

        self.usage_bar = UsageBar(self)
        self.usage_bar.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="ew")

    def _build_controls(self):
        S = self._scale
        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=2, column=0, padx=20, pady=(0, 10), sticky="ew")
        controls.grid_columnconfigure(0, weight=1)

        # рядок 1: пошук · оновити · сортування · напрямок
        line = ctk.CTkFrame(controls, fg_color="transparent")
        line.grid(row=0, column=0, sticky="ew")
        line.grid_columnconfigure(0, weight=1)

        search_box = ctk.CTkFrame(line, corner_radius=10, fg_color=theme.BG_PANEL_LIGHT, height=38)
        search_box.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        search_box.grid_columnconfigure(1, weight=1)
        self._search_icon = ctk.CTkImage(aa.glyph("search", theme.TEXT_DIM, 16, S), size=(16, 16))
        ctk.CTkLabel(search_box, image=self._search_icon, text="", width=16).grid(row=0, column=0, padx=(12, 0), pady=6)
        self.search_entry = ctk.CTkEntry(
            search_box, placeholder_text=t("programs.search"), border_width=0,
            fg_color="transparent", height=34,
        )
        self.search_entry.grid(row=0, column=1, sticky="ew", padx=(4, 8), pady=2)
        self.search_entry.bind("<KeyRelease>", self._on_search_key)

        self._refresh_icon = ctk.CTkImage(aa.glyph("refresh", theme.TEXT_MAIN, 18, S), size=(18, 18))
        self.refresh_button = ctk.CTkButton(
            line, text="", image=self._refresh_icon, width=38, height=38, corner_radius=10,
            fg_color=theme.BG_PANEL_LIGHT, hover_color=theme.BORDER, command=self._load,
        )
        self.refresh_button.grid(row=0, column=1, padx=(0, 8))

        self.sort_menu = ctk.CTkOptionMenu(
            line, values=list(SORT_LABELS.values()), command=self._on_sort_selected, width=196, height=38,
            corner_radius=10, fg_color=theme.BG_PANEL_LIGHT, button_color=theme.BG_PANEL_LIGHT,
            button_hover_color=theme.BORDER, dropdown_fg_color=theme.BG_PANEL,
            dropdown_hover_color=theme.BORDER, text_color=theme.TEXT_MAIN, dropdown_text_color=theme.TEXT_MAIN,
        )
        self.sort_menu.set(SORT_LABELS[self._sort_key])
        self.sort_menu.grid(row=0, column=2, padx=(0, 8))

        self.direction_button = ctk.CTkButton(
            line, text="▼", width=38, height=38, corner_radius=10, fg_color=theme.BG_PANEL_LIGHT,
            hover_color=theme.BORDER, text_color=theme.TEXT_MAIN, command=self._toggle_direction,
        )
        self.direction_button.grid(row=0, column=3)

        # рядок 2: чіпи-фільтри та лічильник
        chips_line = ctk.CTkFrame(controls, fg_color="transparent")
        chips_line.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self._chips: dict[str, ctk.CTkButton] = {}
        for key, text in (
            ("all", t("programs.filter.all")), ("game", t("programs.filter.games")), ("app", t("tabs.programs")),
            ("big", t("programs.filter.big")), ("old", t("programs.filter.old")),
        ):
            chip = ctk.CTkButton(
                chips_line, text=text, height=30, corner_radius=15, width=10,
                font=theme.font_small(), command=lambda k=key: self._on_chip(k),
            )
            chip.pack(side="left", padx=(0, 8))
            self._chips[key] = chip
        self.count_label = ctk.CTkLabel(chips_line, text="", font=theme.font_small(), text_color=theme.TEXT_DIM)
        self.count_label.pack(side="right")
        self._update_chips()

    def _build_list(self):
        self.list = VirtualList(
            self, selected=self._selected, on_toggle=self._on_toggle,
            on_open=self._open_install_folder, on_uninstall=self._uninstall_one,
        )
        self.list.grid(row=3, column=0, padx=(20, 14), pady=(0, 10), sticky="nsew")
        self.list.set_empty_text(t("common.loading"))

    def _build_footer(self):
        footer = ctk.CTkFrame(self, corner_radius=14)
        footer.grid(row=4, column=0, padx=20, pady=(0, 16), sticky="ew")
        footer.grid_columnconfigure(1, weight=1)

        self.selected_label = ctk.CTkLabel(footer, text=t("programs.selected_zero"), font=theme.font_header())
        self.selected_label.grid(row=0, column=0, padx=(16, 8), pady=12)

        self.clear_button = ctk.CTkButton(
            footer, text=t("programs.clear_selection"), width=96, height=30, corner_radius=8, font=theme.font_small(),
            fg_color="transparent", hover_color=theme.BORDER, text_color=theme.TEXT_DIM,
            command=self._clear_selection,
        )
        self.clear_button.grid(row=0, column=1, sticky="w")
        self.clear_button.grid_remove()

        self.delete_selected_button = ctk.CTkButton(
            footer, text=t("programs.remove_selected"), height=34, corner_radius=10, fg_color=theme.ERROR,
            hover_color="#e04a68", text_color="#ffffff", state="disabled", command=self._uninstall_selected,
        )
        self.after(0, self._update_footer)  # потік UI — звичайний after
        self.delete_selected_button.grid(row=0, column=2, padx=16, pady=10)

    # ---------------------------------------------------------- фільтри/сортування

    def _update_chips(self):
        active = {
            "all": self._category_filter == "all" and not self._filter_big and not self._filter_old,
            "game": self._category_filter == "game",
            "app": self._category_filter == "app",
            "big": self._filter_big,
            "old": self._filter_old,
        }
        for key, chip in self._chips.items():
            if active[key]:
                chip.configure(fg_color=theme.ACCENT_GREEN, hover_color=theme.ACCENT_GREEN_DIM, text_color=theme.BG_MAIN)
            else:
                chip.configure(fg_color=theme.BG_PANEL_LIGHT, hover_color=theme.BORDER, text_color=theme.TEXT_MAIN)

    def _on_chip(self, key: str):
        if key == "all":
            self._category_filter, self._filter_big, self._filter_old = "all", False, False
        elif key in ("game", "app"):
            self._category_filter = "all" if self._category_filter == key else key
        elif key == "big":
            self._filter_big = not self._filter_big
        elif key == "old":
            self._filter_old = not self._filter_old
        self._update_chips()
        self._apply_filters()

    def _on_search_key(self, _event=None):
        if self._search_job is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(SEARCH_DEBOUNCE_MS, self._apply_filters)

    def _on_sort_selected(self, label: str):
        key = next(k for k, v in SORT_LABELS.items() if v == label)
        if key != self._sort_key:
            self._sort_key = key
            self._sort_desc = SORT_DEFAULT_DESC[key]
            self._update_direction()
        self._apply_filters()

    def _toggle_direction(self):
        self._sort_desc = not self._sort_desc
        self._update_direction()
        self._apply_filters()

    def _update_direction(self):
        self.direction_button.configure(text="▼" if self._sort_desc else "▲")

    def _sort_field(self, program: dict):
        """(порожнє?, значення): програми без значення завжди в кінці списку."""
        if self._sort_key == "name":
            return False, program["name"].lower()
        if self._sort_key == "size":
            return not program["size_bytes"], program["size_bytes"]
        install_date = program.get("install_date")
        return install_date is None, install_date

    def _sorted(self, programs: list[dict]) -> list[dict]:
        fields = [(self._sort_field(p), p) for p in programs]
        filled = [(f[1], p) for f, p in fields if not f[0]]
        empty = [p for f, p in fields if f[0]]
        filled.sort(key=lambda item: item[0], reverse=self._sort_desc)
        return [p for _v, p in filled] + empty

    def _matches(self, program: dict, query: str, today: date) -> bool:
        is_game = program.get("category") == "game"
        if self._category_filter == "game" and not is_game:
            return False
        if self._category_filter == "app" and is_game:
            return False
        if self._filter_big and program["size_bytes"] < BIG_PROGRAM_BYTES:
            return False
        if self._filter_old:
            installed = program.get("install_date")
            if installed is None or (today - installed).days < OLD_PROGRAM_DAYS:
                return False
        if query and query not in program["name"].lower() and query not in (program.get("publisher") or "").lower():
            return False
        return True

    def _apply_filters(self, keep_scroll: bool = False):
        self._search_job = None
        query = self.search_entry.get().strip().lower()
        today = date.today()
        filtered = self._sorted([p for p in self.all_programs if self._matches(p, query, today)])

        if not self._loaded:
            empty_text = t("common.loading")
        elif not self.all_programs:
            empty_text = t("programs.none")
        else:
            empty_text = t("programs.nothing_found")
        self.list.max_size = max((p["size_bytes"] for p in self.all_programs), default=0)
        self.list.set_items(filtered, keep_scroll=keep_scroll, empty_text=empty_text)
        self.count_label.configure(text=t("programs.shown", count=len(filtered)) if self._loaded else "")
        self._update_summary()

    # ------------------------------------------------------------ підсумки

    def _schedule_summary(self):
        if self._summary_job is None:
            self._summary_job = self.after(SUMMARY_DEBOUNCE_MS, self._on_summary_due)

    def _on_summary_due(self):
        self._summary_job = None
        if not self.winfo_exists():
            return
        self.list.max_size = max((p["size_bytes"] for p in self.all_programs), default=0)
        self.list.refresh_visible()
        self._update_summary()

    def _update_summary(self):
        sizes = {key: 0 for key, _l, _c in CATEGORIES}
        for program in self.all_programs:
            sizes[program.get("category", "other")] += program["size_bytes"]
        approx = any(p.get("size_source") in ("folder", "steam", "computing") for p in self.all_programs)
        self.usage_bar.set_data(sizes, approx)

        if not self._loaded:
            return
        count = len(self.all_programs)
        total = sum(sizes.values())
        text = t("programs.count", count=count)
        if total:
            text += f" · {'~' if approx else ''}{format_size(total)}"
        self.status_label.configure(text=text)

    # -------------------------------------------------------------- load

    def _post(self, func, *args):
        """Виклик у потоці UI з фонового потоку (через чергу ui/bg.py — ніколи не губиться)."""
        bg.ui_call(self, func, *args)

    def _load(self):
        if self._busy or (self._load_task is not None and not self._load_task.finished):
            return
        self.refresh_button.configure(state="disabled")
        self.retry_button.pack_forget()
        self.status_label.configure(text=t("programs.loading_list"), text_color=theme.TEXT_DIM)
        if not self._loaded:
            self.list.set_empty_text(t("common.loading"))
        self._load_task = bg.run_task(
            self, "Programs: installed list", programs_core.list_installed_programs,
            self._on_loaded, self._on_load_failed, timeout=LOAD_TIMEOUT_S,
        )

    def _on_load_failed(self, exc: BaseException):
        if not self.winfo_exists():
            return
        self.refresh_button.configure(state="normal")
        self.status_label.configure(text=t("programs.load_failed", exc=bg.error_text(exc)),
                                    text_color=theme.ERROR)
        self.retry_button.pack(anchor="w", pady=(6, 0))
        if not self._loaded:
            self.list.set_empty_text(t("programs.load_failed_retry"))

    def _on_loaded(self, programs: list[dict]):
        if not self.winfo_exists():
            return
        self.retry_button.pack_forget()
        self.status_label.configure(text_color=theme.TEXT_DIM)
        self.refresh_button.configure(state="normal")
        self._loaded = True
        self.all_programs = programs
        self._by_key = {p["key"]: p for p in programs}
        self._selected.intersection_update(self._by_key)
        self._start_size_computation()  # спершу позначає "рахую…", потім перебудовує список
        self._apply_filters(keep_scroll=True)
        self._update_footer()

    def _start_size_computation(self):
        """Для програм без EstimatedSize і без даних Steam рахує розмір теки
        встановлення у фоновому потоці (послідовно, щоб не навантажувати диск
        паралельними обходами), показуючи "рахую..." доки триває підрахунок.
        """
        pending = [
            p for p in self.all_programs
            if p["size_bytes"] == 0 and p.get("size_source") is None and p.get("install_folder")
        ]
        to_compute = []
        for program in pending:
            # без підтек, що вже є окремими рядками (ігри в теці Steam тощо) — щоб не рахувати двічі
            program["size_exclude"] = programs_core.folder_size_exclusions(program, self.all_programs)
            cached = programs_core.cached_folder_size(program["install_folder"], program["size_exclude"])
            if cached is not None:
                program["size_bytes"] = cached
                program["size_source"] = "folder"
            else:
                program["size_source"] = "computing"
                to_compute.append(program)
        if not to_compute:
            return

        def worker():
            for program in to_compute:
                if self._abort_sizes.is_set():  # вкладку закрито (winfo_* з потоку не можна)
                    return
                size = programs_core.compute_folder_size(program["install_folder"], program["size_exclude"])
                self._post(self._on_size_computed, program["key"], size)

        bg.start_thread(self, "Programs: folder sizes", worker)

    def _on_size_computed(self, key: str, size_bytes: int):
        if not self.winfo_exists():
            return
        program = self._by_key.get(key)
        if not program:
            return
        program["size_bytes"] = size_bytes
        program["size_source"] = "folder"
        self.list.refresh_key(key)
        self._update_footer()
        self._schedule_summary()

    # ----------------------------------------------------------- вибір

    def _on_toggle(self, program: dict, checked: bool):
        if checked:
            self._selected.add(program["key"])
        else:
            self._selected.discard(program["key"])
        self._update_footer()

    def _clear_selection(self):
        self._selected.clear()
        self.list.refresh_selection()
        self._update_footer()

    def _update_footer(self):
        count = len(self._selected)
        text = t("programs.selected", count=count)
        if count:
            total = sum(self._by_key[k]["size_bytes"] for k in self._selected if k in self._by_key)
            if total:
                text += f" · {format_size(total)}"
            self.clear_button.grid()
        else:
            self.clear_button.grid_remove()
        self.selected_label.configure(text=text)
        if count and not self._busy:
            self.delete_selected_button.configure(
                state="normal", fg_color=theme.ERROR, hover_color="#e04a68", text_color="#ffffff",
            )
        else:
            self.delete_selected_button.configure(
                state="disabled", fg_color=theme.BORDER, text_color_disabled=theme.TEXT_DIM,
            )

    # ----------------------------------------------------------- дії

    def _uninstall_one(self, program: dict):
        if self._busy:
            return
        if program.get("steam_appid"):
            note = t("programs.steam_note")
        else:
            note = t("programs.wizard_note")
        if not messagebox.askyesno(t("common.confirmation"), t("programs.confirm_one", name=program['name'], note=note), parent=self):
            return
        self._run_uninstall_queue([program])

    def _uninstall_selected(self):
        programs = [self._by_key[k] for k in self._selected if k in self._by_key]
        if not programs or self._busy:
            return
        programs.sort(key=lambda p: p["name"].lower())
        names = "\n".join(f"• {p['name']}" for p in programs[:8])
        if len(programs) > 8:
            names += t("game_mode.and_more", count=len(programs) - 8)
        confirmed = messagebox.askyesno(
            t("common.confirmation"),
            t("programs.confirm_many", count=len(programs), names=names),
            parent=self,
        )
        if confirmed:
            self._run_uninstall_queue(programs)

    def _run_uninstall_queue(self, programs: list[dict]):
        self._busy = True
        self._abort.clear()
        self._update_footer()
        total = len(programs)

        dialog = CleanerBotDialog(
            self.winfo_toplevel(), title=t("programs.removing_many") if total > 1 else t("programs.removing_one"),
            show_freed_counter=False,
        )
        dialog.start(t("programs.removing", name=programs[0]['name']))
        if total == 1:
            dialog.set_indeterminate(True)
        dialog.allow_cancel(self._abort.set, t("programs.dont_wait"))

        def worker():
            removed = []
            for i, program in enumerate(programs):
                if self._abort.is_set():
                    break
                label = t("programs.removing", name=program['name']) + (f" ({i + 1}/{total})" if total > 1 else "")
                self._post(self._dialog_progress, dialog, label, i / total)
                ok, _error = programs_core.uninstall_and_wait(program, self._abort.is_set)
                if ok:
                    removed.append(program["key"])
            try:
                fresh = programs_core.list_installed_programs()
            except Exception:
                fresh = None  # _on_queue_done лишить старий список; сама помилка вже в logs.txt
                raise
            finally:
                self._post(self._on_queue_done, dialog, programs, removed, fresh)

        bg.start_thread(self, "Programs: uninstall queue", worker)

    def _dialog_progress(self, dialog, label: str, progress: float):
        if dialog.winfo_exists():
            dialog.set_status(label)
            if progress > 0 or dialog.bot.progress.cget("mode") != "indeterminate":
                dialog.set_progress(progress)

    def _on_queue_done(self, dialog, programs: list[dict], removed: list[str], fresh: list[dict]):
        self._busy = False
        if not self.winfo_exists():
            return
        # ключі, що зникли з реєстру, рахуємо за свіжим списком (деінсталятор міг завершитись пізніше)
        if fresh is None:  # не вдалося перечитати реєстр — судимо за результатами деінсталяторів
            fresh_keys = {p["key"] for p in self.all_programs} - set(removed)
        else:
            fresh_keys = {p["key"] for p in fresh}
        removed_count = sum(1 for p in programs if p["key"] not in fresh_keys)

        if dialog.winfo_exists():
            total = len(programs)
            if total == 1:
                name = programs[0]["name"]
                if removed_count:
                    dialog.finish(t("programs.removed", name=name), success=True)
                else:
                    dialog.finish(t("programs.cancelled"), success=False)
            else:
                dialog.finish(t("programs.removed_count", count=removed_count, total=total), success=removed_count > 0)

        if fresh is None:
            self._on_load_failed(RuntimeError("could not re-read the list after uninstalling"))
        else:
            self._on_loaded(fresh)

    def _open_install_folder(self, program: dict):
        folder = program.get("install_folder")
        if not folder:
            messagebox.showinfo(
                t("programs.location_unknown"),
                t("programs.no_folder", name=program['name']),
                parent=self,
            )
            return
        try:
            os.startfile(folder)
        except OSError as exc:
            messagebox.showerror(t("common.error"), t("programs.open_folder_failed", exc=exc), parent=self)

    def is_busy(self) -> bool:
        """Триває операція, яку не можна перервати перебудовою вкладки (зміна мови)."""
        return bool(self._busy)
