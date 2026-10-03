"""Блок «Фонові програми для закриття» вкладки «Ігровий режим» (коли режим вимкнено):
перемикач рівня (М'який / Збалансований / Максимальний) з RAM/CPU для кожного,
групи (Браузери, Месенджери, Лаунчери, Хмари, Периферія, Інше) зі згортанням,
на кожному чіпі — перемикач «закривати / не закривати», «+ Додати програму» і
список «Ніколи не закривати». Сам нічого не закриває і не зберігає — лише
показує дані та повідомляє вкладці про дії користувача через колбеки."""

import os
from tkinter import filedialog

import customtkinter as ctk

from core import app_catalog as catalog
from core import monitor as monitor_core
from core import smart_apps
from ui.widgets.scroll import ScrollFrame
from core.i18n import t
from ui import bg, theme
from ui.widgets.game_widgets import ChipBoard, fmt_mem

_ICON = 20
_TITLE_CHARS = 30


def _stats(memory_mb: float, cpu: float) -> str:
    text = fmt_mem(memory_mb) if memory_mb else "—"
    if cpu >= 0.5:
        text += f" · {cpu:.0f}% CPU"
    return text


def _short(text: str, limit: int = _TITLE_CHARS) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


class _IconStore:
    """exe_path -> CTkImage; вантажить у фоні, повідомляє on_ready(path)."""

    def __init__(self, owner, on_ready):
        self._owner, self._on_ready = owner, on_ready
        self._images: dict[str, object] = {}
        self._requested: set[str] = set()

    def get(self, path):
        return self._images.get(path) if path else None

    def request(self, paths) -> None:
        todo = [p for p in paths if p and p not in self._requested]
        if not todo:
            return
        self._requested.update(todo)

        def worker():
            from core.app_icons import extract_icon
            for path in todo:
                try:
                    image = extract_icon(path)
                except Exception:
                    image = None
                if image is not None:
                    bg.ui_call(self._owner, self._store, path, image)

        bg.start_thread(self._owner, "Game Mode: program icons", worker)

    def _store(self, path, pil_image) -> None:
        self._images[path] = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(_ICON, _ICON))
        self._on_ready(path)


class AppChip(ctk.CTkFrame):
    """Чіп програми: перемикач «закривати», іконка, назва, RAM/CPU, примітка; ✕ — для доданих вручну."""

    def __init__(self, master, key: str, on_toggle, on_remove=None):
        super().__init__(master, fg_color=theme.BG_PANEL_LIGHT, corner_radius=10)
        self.key = key
        self.exe_path = None
        self.grid_columnconfigure(2, weight=1)
        self.switch = ctk.CTkSwitch(self, text="", width=40, progress_color=theme.ACCENT_GREEN,
                                    command=lambda: on_toggle(key, self.var.get()))
        self.var = bg.WidgetBool(self.switch)  # без Tk-змінної: чіпи створюються й знищуються динамічно
        self.var.set(True)
        self.switch.grid(row=0, column=0, padx=(10, 0), pady=6)
        self.icon = ctk.CTkLabel(self, text="", width=_ICON, height=_ICON)
        self.icon.grid(row=0, column=1, padx=(0, 6))
        self.title = ctk.CTkLabel(self, text="", anchor="w", font=ctk.CTkFont(size=12, weight="bold"))
        self.title.grid(row=0, column=2, sticky="ew")
        self.stats = ctk.CTkLabel(self, text="", font=theme.font_small(), text_color=theme.TEXT_DIM)
        self.stats.grid(row=0, column=3, padx=(6, 10 if on_remove is None else 2))
        if on_remove is not None:
            ctk.CTkButton(self, text="✕", width=22, height=22, fg_color="transparent", hover_color=theme.BORDER,
                          text_color=theme.TEXT_DIM, command=lambda: on_remove(key)).grid(row=0, column=4, padx=(0, 6))
        self.note = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=10), anchor="w", justify="left",
                                 wraplength=380)
        self.note.grid(row=1, column=1, columnspan=4, padx=(0, 10), pady=(0, 6), sticky="w")
        self.note.grid_remove()

    def update_app(self, app: dict, icon) -> None:
        closing = bool(app.get("close", True))
        if bool(self.var.get()) != closing:
            self.var.set(closing)
        self.exe_path = app.get("exe_path")
        self.title.configure(text=_short(app["title"]), text_color=theme.TEXT_MAIN if closing else theme.TEXT_DIM)
        if not app.get("running", True):
            self.stats.configure(text=t("game_mode_apps.not_running"))
        else:
            self.stats.configure(text=_stats(app.get("memory_mb", 0), app.get("cpu_percent", 0)))
        self.set_icon(icon)
        note, color = "", theme.TEXT_DIM
        if app.get("warning"):
            note, color = "⚠ " + app["warning"], theme.WARNING
        elif app.get("document"):
            note = t(catalog.DOCUMENT_NOTE)
        elif app.get("category") in ("user", "profile"):
            note = catalog.CATEGORY_LABELS[app["category"]]
        if note:
            self.note.configure(text=note, text_color=color)
            self.note.grid()
        else:
            self.note.grid_remove()

    def set_icon(self, icon) -> None:
        self.icon.configure(image=icon, text="" if icon else "•")


class _GroupBox(ctk.CTkFrame):
    """Група: заголовок-кнопка «▾ Браузери · 3 · 1,2 ГБ» і сітка чіпів у 2 колонки."""

    def __init__(self, master, group: str, on_header):
        super().__init__(master, fg_color="transparent")
        self.group = group
        self.header = ctk.CTkButton(self, text="", anchor="w", height=28, fg_color="transparent",
                                    hover_color=theme.BG_PANEL_LIGHT, text_color=theme.TEXT_MAIN,
                                    font=ctk.CTkFont(size=13, weight="bold"), command=lambda: on_header(group))
        self.header.pack(fill="x")
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid_columnconfigure((0, 1), weight=1, uniform="chips")
        self.order: list[str] = []


class AppsPanel(ctk.CTkFrame):
    def __init__(self, master, on_level, on_toggle, on_add, on_remove_user, on_group_toggle,
                 on_never_add, on_never_remove, on_never_reset):
        super().__init__(master, fg_color="transparent")
        self._cb = {"level": on_level, "toggle": on_toggle, "add": on_add, "remove_user": on_remove_user,
                    "group": on_group_toggle, "never_add": on_never_add, "never_remove": on_never_remove,
                    "never_reset": on_never_reset}
        self._icons = _IconStore(self, self._icon_ready)
        self._chips: dict[str, AppChip] = {}
        self._groups: dict[str, _GroupBox] = {}
        self._never_open = False
        self.grid_columnconfigure(0, weight=1)

        self.level_button = ctk.CTkSegmentedButton(
            self, values=[catalog.LEVEL_LABELS[lv] for lv in catalog.LEVELS], height=30,
            selected_color=theme.ACCENT_BLUE_DIM, selected_hover_color=theme.ACCENT_BLUE_DIM,
            unselected_color=theme.BG_PANEL_LIGHT, unselected_hover_color=theme.BORDER,
            command=self._on_level_label,
        )
        self.level_button.grid(row=0, column=0, sticky="w")
        self.level_stats = ctk.CTkLabel(self, text="", font=theme.font_small(), text_color=theme.TEXT_DIM,
                                        anchor="w", justify="left")
        self.level_stats.grid(row=1, column=0, pady=(6, 0), sticky="w")
        self.level_hint = ctk.CTkLabel(self, text="", font=theme.font_small(), text_color=theme.TEXT_DIM,
                                       anchor="w", justify="left", wraplength=760)
        self.level_hint.grid(row=2, column=0, pady=(2, 8), sticky="w")

        self.groups_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.groups_frame.grid(row=3, column=0, sticky="ew")
        self.groups_frame.grid_columnconfigure(0, weight=1)
        self.empty_label = ctk.CTkLabel(self, text="", font=theme.font_body(), text_color=theme.TEXT_DIM, anchor="w")

        self.skipped_label = ctk.CTkLabel(self, text="", font=theme.font_small(), text_color=theme.TEXT_DIM,
                                          anchor="w", justify="left", wraplength=760)
        self.skipped_label.grid(row=5, column=0, pady=(6, 0), sticky="w")

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=6, column=0, pady=(10, 0), sticky="w")
        self._button(actions, t("game_mode_apps.add_program"), lambda: self._open_picker("add")).pack(side="left")
        self.never_button = self._button(actions, "", self._toggle_never, fg_color="transparent",
                                         hover_color=theme.BG_PANEL_LIGHT, text_color=theme.ACCENT_BLUE)
        self.never_button.pack(side="left", padx=(8, 0))

        self.never_frame = ctk.CTkFrame(self, fg_color=theme.BG_PANEL_LIGHT, corner_radius=10)
        ctk.CTkLabel(self.never_frame, text=t("game_mode_apps.never_hint"), font=theme.font_small(),
                     text_color=theme.TEXT_DIM, anchor="w", justify="left", wraplength=740).pack(
            fill="x", padx=12, pady=(10, 6))
        self.never_chips = ChipBoard(self.never_frame, on_remove=lambda key: self._cb["never_remove"](key),
                                     max_rows=12, bg=theme.BG_PANEL_LIGHT)
        self.never_chips.pack(fill="x", padx=12)
        never_actions = ctk.CTkFrame(self.never_frame, fg_color="transparent")
        never_actions.pack(fill="x", padx=12, pady=(6, 10))
        self._button(never_actions, t("game_mode_apps.add"), lambda: self._open_picker("never")).pack(side="left")
        self._button(never_actions, t("game_mode_apps.restore_defaults"), lambda: self._cb["never_reset"](),
                     fg_color="transparent", hover_color=theme.BORDER,
                     text_color=theme.ACCENT_BLUE).pack(side="left", padx=(8, 0))

    def _button(self, parent, text, command, **style):
        options = {"fg_color": theme.BG_PANEL_LIGHT, "hover_color": theme.BORDER, "text_color": theme.TEXT_MAIN}
        options.update(style)
        return ctk.CTkButton(parent, text=text, height=28, corner_radius=8, width=10, font=theme.font_small(),
                             command=command, **options)

    # ------------------------------------------------------------ render

    def render(self, level: str, plans: dict, apps: list[dict], user_keys: set, collapsed,
               never_patterns: list[str], never_is_default: bool) -> None:
        """apps — готовий список чіпів поточного рівня (план + додані вручну + процеси профілю)."""
        label = catalog.LEVEL_LABELS[level]
        if self.level_button.get() != label:
            self.level_button.set(label)
        parts = []
        for lv in catalog.LEVELS:
            plan = plans[lv]
            text = f"{catalog.LEVEL_LABELS[lv]}: {_stats(plan['memory_mb'], plan['cpu_percent'])}"
            parts.append(f"▸ {text}" if lv == level else text)
        self.level_stats.configure(text=t("game_mode_apps.will_free") + "    ".join(parts))
        self.level_hint.configure(text=catalog.LEVEL_HINTS[level])

        self._render_groups(apps, set(collapsed), set(user_keys))

        skipped = plans[level]["skipped"]
        self.skipped_label.configure(text=t("game_mode_apps.not_closing") + "; ".join(f"{tw} — {r}" for tw, r in skipped)
                                     if skipped else "")
        (self.skipped_label.grid if skipped else self.skipped_label.grid_remove)()

        self.never_button.configure(text=t("game_mode_apps.never_button", arrow="▾" if self._never_open else "▸",
                                           count=len(never_patterns),
                                           changed="" if never_is_default else t("game_mode_apps.changed_suffix")))
        self.never_chips.set_chips([{"key": p, "title": p, "memory_mb": 0, "icon": False} for p in never_patterns],
                                   True, t("game_mode_apps.never_empty"))

    def _render_groups(self, apps: list[dict], collapsed: set, user_keys: set) -> None:
        by_group: dict[str, list[dict]] = {g: [] for g in catalog.GROUPS}
        for app in apps:
            by_group[app.get("group") or catalog.G_OTHER].append(app)
        self._icons.request(a.get("exe_path") for a in apps)

        alive = set()
        row = 0
        for group in catalog.GROUPS:
            items = by_group[group]
            box = self._groups.get(group)
            if not items:
                if box is not None:
                    box.grid_remove()
                continue
            if box is None:
                box = self._groups[group] = _GroupBox(self.groups_frame, group, self._cb["group"])
            box.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            row += 1
            closing = [a for a in items if a.get("close", True) and a.get("running", True)]
            is_collapsed = group in collapsed
            box.header.configure(text=f"{'▸' if is_collapsed else '▾'}  {catalog.GROUP_LABELS[group]} · "
                                      f"{len(items)} · {fmt_mem(sum(a.get('memory_mb', 0) for a in closing))}")
            order = [a["key"] for a in items]
            for app in items:
                key = app["key"]
                alive.add(key)
                chip = self._chips.get(key)
                removable = key in user_keys or app.get("category") == "profile"
                if chip is None or chip.master is not box.body:
                    if chip is not None:
                        chip.destroy()
                    chip = self._chips[key] = AppChip(box.body, key, self._cb["toggle"],
                                                      self._cb["remove_user"] if removable else None)
                chip.update_app(app, self._icons.get(app.get("exe_path")))
            if order != box.order:
                for chip_key in box.order:
                    if chip_key in self._chips and chip_key in order:
                        self._chips[chip_key].grid_forget()
                for i, key in enumerate(order):
                    self._chips[key].grid(row=i // 2, column=i % 2, padx=(0, 6), pady=3, sticky="ew")
                box.order = order
            if is_collapsed:
                box.body.pack_forget()
            elif not box.body.winfo_manager():
                box.body.pack(fill="x", padx=(8, 0))

        for key in [k for k in self._chips if k not in alive]:
            self._chips.pop(key).destroy()
        for box in self._groups.values():
            box.order = [k for k in box.order if k in alive]
        if row == 0:
            self.empty_label.configure(text=t("game_mode_apps.nothing_to_close"))
            self.empty_label.grid(row=4, column=0, sticky="w")
        else:
            self.empty_label.grid_remove()

    def _icon_ready(self, path) -> None:
        for chip in self._chips.values():
            if chip.exe_path == path:
                chip.set_icon(self._icons.get(path))

    # ------------------------------------------------------------ дії

    def _on_level_label(self, label: str) -> None:
        level = next(lv for lv in catalog.LEVELS if catalog.LEVEL_LABELS[lv] == label)
        self._cb["level"](level)

    def _toggle_never(self) -> None:
        self._never_open = not self._never_open
        if self._never_open:
            self.never_frame.grid(row=7, column=0, pady=(8, 0), sticky="ew")
        else:
            self.never_frame.grid_remove()
        text = self.never_button.cget("text")
        self.never_button.configure(text=("▾" if self._never_open else "▸") + text[1:])

    def _open_picker(self, mode: str) -> None:
        def chosen(exe_name, title, exe_path):
            if mode == "add":
                self._cb["add"](exe_name.lower(), title, exe_path)
            else:
                self._cb["never_add"](exe_name.lower())

        AddAppDialog(self.winfo_toplevel(), mode, chosen)


class AddAppDialog(ctk.CTkToplevel):
    """Вибір програми: запущені (з пошуком та іконками) або exe вручну."""

    def __init__(self, master, mode: str, on_choose):
        super().__init__(master)
        self._on_choose = on_choose
        self._rows: list[tuple[ctk.CTkButton, str, str | None]] = []
        self.title(t("game_mode_apps.add_title") if mode == "add" else t("game_mode_apps.never_add_title"))
        self.geometry("480x540")
        self.configure(fg_color=theme.BG_PANEL)
        self.transient(master)
        ctk.CTkLabel(self, text=(t("game_mode_apps.pick_running")
                                 if mode == "add" else t("game_mode_apps.pick_never")),
                     font=theme.font_body(), wraplength=440, justify="left").pack(padx=16, pady=(14, 6), anchor="w")
        self.search = ctk.CTkEntry(self, placeholder_text=t("game_mode_apps.search"))
        self.search.pack(fill="x", padx=16)
        self.search.bind("<KeyRelease>", lambda _e: self._filter())
        self.list = ScrollFrame(self, bg=theme.BG_MAIN)
        self.list.pack(fill="both", expand=True, padx=16, pady=8)
        self.status = ctk.CTkLabel(self.list, text=t("game_mode_apps.loading"), text_color=theme.TEXT_DIM)
        self.status.pack(pady=20)
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkButton(bottom, text=t("game_mode_apps.manual_exe"), width=10, fg_color=theme.BG_PANEL_LIGHT,
                      hover_color=theme.BORDER, command=self._browse).pack(side="left")
        ctk.CTkButton(bottom, text=t("common.close"), width=90, fg_color="transparent", border_width=1,
                      command=self.destroy).pack(side="right")
        self._icons = _IconStore(self, self._icon_ready)
        self.after(50, self._grab)
        bg.run_task(self, "Game Mode: running program list", monitor_core.get_process_groups,
                    self._fill, self._failed, timeout=30)

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.search.focus_set()
        except Exception:
            pass

    def _failed(self, exc) -> None:
        self.status.configure(text=t("game_mode_apps.load_failed", exc=bg.error_text(exc)))

    def _fill(self, groups: list[dict]) -> None:
        if not self.winfo_exists():
            return
        self.status.pack_forget()
        seen = set()
        items = []
        for group in sorted(groups, key=lambda g: g["memory_mb"], reverse=True):
            name = group["name"]
            if name.lower() in seen or smart_apps.is_hard_never(name):
                continue
            seen.add(name.lower())
            items.append(group)
        for group in items:
            title = catalog.NICE_TITLES.get(group["name"].lower(), group["title"])
            text = f"{_short(title, 34)}   ·   {group['name']} · {fmt_mem(group['memory_mb'])}"
            button = ctk.CTkButton(
                self.list, text=text, anchor="w", height=30, fg_color="transparent", hover_color=theme.BG_PANEL_LIGHT,
                text_color=theme.TEXT_MAIN, image=None, compound="left",
                command=lambda g=group, tw=title: self._choose(g["name"], tw, g.get("exe_path")),
            )
            button.pack(fill="x")
            self._rows.append((button, f"{title} {group['name']}".lower(), group.get("exe_path")))
        if not items:
            self.status.configure(text=t("game_mode_apps.none_running"))
            self.status.pack(pady=20)
        self._icons.request(path for _b, _t, path in self._rows)

    def _icon_ready(self, path) -> None:
        for button, _text, row_path in self._rows:
            if row_path == path:
                button.configure(image=self._icons.get(path))

    def _filter(self) -> None:
        query = self.search.get().strip().lower()
        for button, text, _path in self._rows:
            button.pack_forget()
        for button, text, _path in self._rows:
            if not query or query in text:
                button.pack(fill="x")

    def _browse(self) -> None:
        path = filedialog.askopenfilename(parent=self, title=t("game_mode_apps.pick_exe"),
                                          filetypes=[(t("tabs.programs"), "*.exe"), (t("common.all_files"), "*.*")])
        if path:
            name = os.path.basename(path)
            self._choose(name, os.path.splitext(name)[0], os.path.normpath(path))

    def _choose(self, exe_name: str, title: str, exe_path) -> None:
        self.destroy()
        self._on_choose(exe_name, title, exe_path)
