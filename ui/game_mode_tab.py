"""Вкладка «Ігровий режим» — у стилі «Монітора»: робот, великий перемикач, розумний
список програм для закриття, знайдені ігри, підсумки сесій. Профілі, ручний
вибір процесів і плани живлення — у згорнутому блоці «Розширені»."""

import copy
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from core import game_mode as game_mode_core
from core import game_scanner, game_sessions, power_plans, process_info, smart_apps
from core import monitor as monitor_core
from core.logging_setup import get_logger
from ui import theme
from ui.widgets.canvas_list import PROCESS_BADGES, CanvasList, card_image, checkbox_image
from ui.widgets.game_widgets import (
    BigSwitch, ChipBoard, GamesList, GameRobot, ScrollPage, SessionsList,
    fmt_mem, fmt_pct, fmt_temp,
)

GAME_CHECK_INTERVAL_SEC = 2.0
PREVIEW_INTERVAL_SEC = 3.0
SESSION_END_GRACE_TICKS = 2  # гра «зникла» на стільки перевірок поспіль — сесія завершена
PROFILE_NAMES = ("Гра", "Стрім", "Робота")
_LIST_HEIGHT_DP = 300
_COMMIT_KEYS = ("is_active", "active_profile", "closed_apps", "freed_mb", "plan_name",
                "previous_power_plan", "ultra_guid")

_logger = get_logger(__name__)


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _fmt_freed(mb: float) -> str:
    return f"~{mb / 1024:.1f} ГБ" if mb >= 1024 else f"~{max(round(mb / 10) * 10, 10):.0f} МБ"


# ======================================================= ручний вибір процесів

_CHECK_ROW_DP = 30
_CHECK_DP = 18


class ProcessCheckList(CanvasList):
    """Список процесів із чекбоксами на одному Canvas (замість сотні
    CTkCheckBox у CTkScrollableFrame). Клік по всьому рядку перемикає пункт,
    як клік по тексту CTkCheckBox; системні процеси — недоступні."""

    wheel_step_dp = _CHECK_ROW_DP * 3
    clickable_regions = frozenset({"row"})

    def __init__(self, master, on_toggle):
        super().__init__(master, bg=theme.BG_PANEL, scrollbar_gap=4)
        self._on_toggle = on_toggle
        self.items: list[dict] = []  # {"name", "label", "checked", "protected"}

    def set_items(self, items: list[dict]) -> None:
        self.items = items
        self.set_count(len(items), keep_scroll=True)

    def row_height_dp(self, index: int) -> float:
        return _CHECK_ROW_DP

    def create_slot(self, slot) -> None:
        c = self.canvas
        base = (slot.tag, slot.base_tag)
        slot.items["bg"] = c.create_image(0, 0, anchor="nw", tags=(slot.tag,))
        slot.items["check"] = c.create_image(0, 0, anchor="w", tags=base)
        slot.items["label"] = c.create_text(0, 0, anchor="w", font=self.font(13), tags=base)
        slot.items["badge_bg"] = c.create_image(0, 0, anchor="w", tags=(slot.tag,))
        slot.items["badge"] = c.create_text(0, 0, anchor="center", font=self.font(10, "bold"), tags=(slot.tag,))

    def bind_slot(self, slot, index: int) -> None:
        item = self.items[index]
        mid = self.px(_CHECK_ROW_DP) // 2
        c = self.canvas
        c.coords(slot.items["check"], self.px(6), mid)
        x = self.px(6 + _CHECK_DP + 10)
        kind = process_info.kind_for(item["name"])
        badge_w = 0
        if kind:
            text, color = PROCESS_BADGES[kind]
            photo, badge_w = self.badge_image(text, color)
        font = self.font(13)
        shown = self.truncate(item["label"], max(self.width - x - self.px(14) - badge_w, 40), font)
        c.coords(slot.items["label"], x, mid)
        c.itemconfigure(slot.items["label"], text=shown,
                        fill=theme.TEXT_DIM if item["protected"] else theme.TEXT_MAIN)
        if kind:
            bx = x + self.text_width(shown, font) + self.px(8)
            c.coords(slot.items["badge_bg"], bx, mid)
            c.itemconfigure(slot.items["badge_bg"], image=photo, state="normal")
            c.coords(slot.items["badge"], bx + badge_w / 2, mid)
            c.itemconfigure(slot.items["badge"], text=text, fill=color, state="normal")

    def hover_slot(self, slot, index: int, region) -> None:
        item = self.items[index]
        hovered = region is not None and not item["protected"]
        c = self.canvas
        if hovered:
            c.itemconfigure(slot.items["bg"], state="normal", image=card_image(
                max(self.width, 20), self.px(_CHECK_ROW_DP), self.px(8),
                theme.BG_PANEL_LIGHT, theme.BG_PANEL_LIGHT, theme.BG_PANEL,
            ))
        else:
            c.itemconfigure(slot.items["bg"], state="hidden")
        c.itemconfigure(slot.items["check"], image=checkbox_image(
            self.px(_CHECK_DP), item["checked"], hovered, item["protected"], self.S,
        ))

    def hit_test(self, index: int, x: int, y: int):
        return "disabled" if self.items[index]["protected"] else "row"

    def row_identity(self, index: int):
        return self.items[index]["name"]

    def tooltip_for(self, index: int, region: str):
        item = self.items[index]
        return process_info.tooltip_text(item["name"], item.get("pid"))

    def click(self, index: int, region: str) -> None:
        if region != "row":
            return
        item = self.items[index]
        if not item["checked"] and process_info.is_anticheat(item["name"]):
            if not messagebox.askyesno(
                "Античит",
                f"«{item['name']}» — античит.\n\n{process_info.ANTICHEAT_WARNING}\n\n"
                "Усе одно закривати його під час увімкнення профілю?",
                parent=self,
            ):
                return
        item["checked"] = not item["checked"]
        slot = self.slot_for(index)
        if slot is not None:
            self.hover_slot(slot, index, region)
        self._on_toggle(item["name"], item["checked"])


# ================================================================== вкладка

class GameModeTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.state = game_mode_core.load_game_mode()
        self._lock = threading.RLock()  # стан читають і міняють UI та фонові потоки
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self._visible = False
        self._busy = False
        self._auto_enabled = False       # режим увімкнено автоматично (гра) — сам і вимкнеться
        self._games: list[dict] = []     # знайдені ігри (core.game_scanner)
        self._running_game_keys: set = set()
        self._candidates: list[dict] = []  # розумний список без урахування вилучених
        self._running_mem: dict[str, float] = {}  # назва exe -> RAM (для ручних процесів профілю)
        self._on_battery = False
        self._note = ""
        self._monitor_tab = None
        self._advanced_built = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.page = ScrollPage(self)
        self.page.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        inner = self.page.inner
        inner.grid_columnconfigure((0, 1), weight=1, uniform="cols")

        ctk.CTkLabel(inner, text="Ігровий режим", font=theme.font_title()).grid(
            row=0, column=0, columnspan=2, padx=20, pady=(20, 8), sticky="w")
        self._build_hero(inner)
        self._build_apps_card(inner)
        self._build_games_card(inner)
        self._build_sessions_card(inner)
        self._build_advanced_card(inner)

        self._render()
        self._render_sessions()
        self.bind("<Destroy>", self._on_destroy)

        # Через after(0, ...), а не напряму: вкладки створюються ще до
        # MainWindow.mainloop(), і потік міг би викликати self.after() ще
        # до реального старту mainloop (Python 3.13+ кидає на це непіймане
        # RuntimeError, і потік мовчки гине).
        self.after(0, self._start_workers)
        self.after(1500, self._check_crash_recovery)

    # ------------------------------------------------------------ побудова

    def _card(self, parent, row: int, column: int = 0, columnspan: int = 2, pady=(0, 14)):
        card = theme.PlainFrame(parent, fg_color=theme.BG_PANEL, corner_radius=14)
        left = 20 if column == 0 else 7
        right = 20 if column + columnspan == 2 else 7
        card.grid(row=row, column=column, columnspan=columnspan, padx=(left, right), pady=pady, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        return card

    def _build_hero(self, inner) -> None:
        card = self._card(inner, 1)
        self.robot = GameRobot(card)
        self.robot.pack(pady=(20, 4))
        self.switch = BigSwitch(card, self._on_switch_clicked)
        self.switch.pack(pady=(6, 8))
        ctk.CTkLabel(card, text="Ігровий режим", font=theme.font_header()).pack()
        self.status_label = ctk.CTkLabel(card, text="", font=ctk.CTkFont(size=14, weight="bold"))
        self.status_label.pack(pady=(2, 6))
        self.summary_label = ctk.CTkLabel(
            card, text="", font=theme.font_body(), text_color=theme.TEXT_MAIN, wraplength=560, justify="center",
        )
        self.summary_label.pack(padx=24)
        self.note_label = ctk.CTkLabel(
            card, text="", font=theme.font_small(), text_color=theme.WARNING, wraplength=560, justify="center",
        )
        self.note_label.pack(padx=24, pady=(6, 20))
        tk.Misc.bind(card, "<Configure>", lambda e: self._fit_wrap(e.width), "+")

    def _fit_wrap(self, width_px: int) -> None:
        wrap = max(round(width_px / self._get_widget_scaling()) - 80, 200)
        for label in (self.summary_label, self.note_label):
            if label.cget("wraplength") != wrap:
                label.configure(wraplength=wrap)

    def _build_apps_card(self, inner) -> None:
        card = self._card(inner, 2)
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, padx=16, pady=(14, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        self.apps_title = ctk.CTkLabel(header, text="Фонові програми для закриття", font=theme.font_header())
        self.apps_title.grid(row=0, column=0, sticky="w")
        self.apps_info = ctk.CTkLabel(header, text="", font=theme.font_small(), text_color=theme.TEXT_DIM)
        self.apps_info.grid(row=0, column=1, sticky="e")
        self.chips = ChipBoard(card, on_remove=self._on_chip_remove)
        self.chips.grid(row=1, column=0, padx=16, pady=(0, 8), sticky="ew")
        self.restore_button = ctk.CTkButton(
            card, text="", height=26, width=10, corner_radius=8, font=theme.font_small(),
            fg_color="transparent", hover_color=theme.BG_PANEL_LIGHT, text_color=theme.ACCENT_BLUE,
            command=self._on_restore_excluded,
        )
        self.restore_button.grid(row=2, column=0, padx=12, pady=(0, 10), sticky="w")
        self.restore_button.grid_remove()

    def _small_button(self, parent, text: str, command, width: int = 90, **options):
        return ctk.CTkButton(parent, text=text, width=width, height=28, font=theme.font_small(),
                             command=command, **options)

    def _build_games_card(self, inner) -> None:
        card = self._card(inner, 3, column=0, columnspan=1)
        card.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, padx=16, pady=(14, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Ігри", font=theme.font_header()).grid(row=0, column=0, sticky="w")
        self.games_info = ctk.CTkLabel(header, text="Шукаю ігри…", font=theme.font_small(), text_color=theme.TEXT_DIM)
        self.games_info.grid(row=0, column=1, padx=(8, 8), sticky="e")
        self._small_button(header, "Усі", lambda: self._set_all_auto(True), width=46).grid(row=0, column=2, padx=(0, 4))
        self._small_button(header, "Жодної", lambda: self._set_all_auto(False), width=60).grid(row=0, column=3, padx=(0, 4))
        self._small_button(header, "Оновити", self._rescan_games, width=72).grid(row=0, column=4)
        self.games_list = GamesList(card, on_toggle=self._on_game_toggle)
        self.games_list.canvas.configure(width=10, height=round(_LIST_HEIGHT_DP * self._get_widget_scaling()))
        self.games_list.grid(row=1, column=0, padx=(12, 8), pady=(0, 6), sticky="nsew")
        self.games_list.set_empty_text("Шукаю встановлені ігри…")
        ctk.CTkLabel(
            card, text="Перемикач «Авто» — вмикати режим, коли гра запускається, і вимикати після виходу.",
            font=theme.font_small(), text_color=theme.TEXT_DIM, wraplength=330, justify="left",
        ).grid(row=2, column=0, padx=16, pady=(0, 12), sticky="w")

    def _build_sessions_card(self, inner) -> None:
        card = self._card(inner, 3, column=1, columnspan=1)
        card.grid_rowconfigure(2, weight=1)
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, padx=16, pady=(14, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Підсумок сесії", font=theme.font_header()).grid(row=0, column=0, sticky="w")
        self._small_button(header, "Очистити", self._clear_sessions, width=76,
                           fg_color=theme.BG_PANEL_LIGHT, hover_color="#a8283f").grid(row=0, column=1)

        self.last_card = theme.PlainFrame(card, fg_color=theme.BG_PANEL_LIGHT, corner_radius=12)
        self.last_card.grid(row=1, column=0, padx=16, pady=(0, 8), sticky="ew")
        self.last_card.grid_columnconfigure((0, 1), weight=1, uniform="tiles")
        self.last_title = ctk.CTkLabel(self.last_card, text="", font=theme.font_header(), anchor="w")
        self.last_title.grid(row=0, column=0, columnspan=2, padx=12, pady=(10, 0), sticky="ew")
        self.last_sub = ctk.CTkLabel(self.last_card, text="", font=theme.font_small(), text_color=theme.TEXT_DIM,
                                     anchor="w", justify="left")
        self.last_sub.grid(row=1, column=0, columnspan=2, padx=12, pady=(0, 6), sticky="ew")
        self._tiles = {}
        for i, (key, title) in enumerate((("cpu", "CPU (сер. / макс.)"), ("gpu", "GPU (сер. / макс.)"),
                                          ("cpu_temp", "Макс. темп. CPU"), ("gpu_temp", "Макс. темп. GPU"))):
            ctk.CTkLabel(self.last_card, text=title, font=theme.font_small(), text_color=theme.TEXT_DIM,
                         anchor="w").grid(row=2 + (i // 2) * 2, column=i % 2, padx=12, sticky="ew")
            value = ctk.CTkLabel(self.last_card, text="—", font=theme.font_header(), anchor="w")
            value.grid(row=3 + (i // 2) * 2, column=i % 2, padx=12, pady=(0, 8), sticky="ew")
            self._tiles[key] = value

        self.sessions_list = SessionsList(card)
        self.sessions_list.canvas.configure(width=10, height=round(150 * self._get_widget_scaling()))
        self.sessions_list.grid(row=2, column=0, padx=(12, 8), pady=(0, 14), sticky="nsew")
        self.sessions_list.set_empty_text("Історія порожня — зіграй у гру, і тут з'явиться підсумок")

    def _build_advanced_card(self, inner) -> None:
        self.adv_card = self._card(inner, 4, pady=(0, 20))
        self.adv_button = ctk.CTkButton(
            self.adv_card, text="▸  Розширені", anchor="w", height=40, corner_radius=14,
            font=theme.font_header(), fg_color="transparent", hover_color=theme.BG_PANEL_LIGHT,
            text_color=theme.TEXT_MAIN, command=self._toggle_advanced,
        )
        self.adv_button.grid(row=0, column=0, padx=6, pady=6, sticky="ew")
        self.adv_body = None

    # ------------------------------------------------------------- потоки

    def _start_workers(self) -> None:
        self._monitor_tab = getattr(self.winfo_toplevel(), "tab_frames", {}).get("monitor")
        for target in (self._preview_loop, self._watch_loop, self._scan_games):
            threading.Thread(target=target, daemon=True).start()

    def _post(self, callback, *args) -> None:
        """Виклик у потоці UI з фонового потоку."""
        if self._stop_event.is_set():
            return
        try:
            self.after(0, callback, *args)
        except (RuntimeError, tk.TclError):
            pass

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            self._stop_event.set()
            self._wake.set()

    def on_visibility_changed(self, visible: bool) -> None:
        self._visible = visible
        self.robot.set_running(visible)
        if visible:
            self._wake.set()
            self._render()

    # ----------------------------------------------- прев'ю (що буде закрито)

    def _preview_loop(self) -> None:
        """Кожні кілька секунд, поки вкладку видно: знімок процесів → кандидати на закриття.
        Лише читає — стан CPU % Монітора не чіпає (track_cpu=False)."""
        while not self._stop_event.is_set():
            if self._visible and not self.state.get("is_active"):
                try:
                    groups = monitor_core.get_process_groups()
                    platforms = {g["platform"] for g in self._games}
                    candidates = smart_apps.compute_suggestions(set(), platforms, groups)
                    mem: dict[str, float] = {}
                    for group in groups:
                        for m in group["members"]:
                            mem[m["name"].lower()] = mem.get(m["name"].lower(), 0.0) + m["memory_mb"]
                    self._post(self._apply_preview, candidates, mem, power_plans.on_battery())
                except Exception:
                    _logger.exception("Не вдалося зібрати список програм для закриття")
            self._wake.wait(PREVIEW_INTERVAL_SEC)
            self._wake.clear()

    def _apply_preview(self, candidates, mem, on_battery) -> None:
        self._candidates, self._running_mem, self._on_battery = candidates, mem, on_battery
        self._render()

    def _profile(self) -> dict:
        return self.state["profiles"].get(self.state["active_profile"], {})

    def _plan_value(self) -> str:
        return self._profile().get("power_plan", "")

    def _current_apps(self) -> list[dict]:
        excluded = {n.lower() for n in self.state.get("excluded_apps", [])}
        return [a for a in self._candidates if a["key"] not in excluded]

    def _current_extras(self) -> list[str]:
        """Вручну позначені в профілі процеси, що зараз запущені (і не входять у розумний список)."""
        in_apps = {a["name"].lower() for a in self._current_apps()}
        return [p for p in self._profile().get("processes", [])
                if p.lower() in self._running_mem and p.lower() not in in_apps]

    # ------------------------------------------------------------ відображення

    def _render(self) -> None:
        if not self.winfo_exists():
            return
        active = bool(self.state.get("is_active"))
        self.robot.set_active(active)
        self.switch.set_on(active)
        self.switch.set_busy(self._busy)

        if self._busy:
            status, color = ("Вимикаю…" if active else "Вмикаю…"), theme.ACCENT_BLUE
        elif active:
            status, color = "● Увімкнено", theme.ACCENT_GREEN
        else:
            status, color = "Вимкнено", theme.TEXT_DIM
        theme.set_text(self.status_label, status, text_color=color)

        apps, extras = self._current_apps(), self._current_extras()
        if active:
            closed = self.state.get("closed_apps", [])
            summary = self._active_summary(len(closed))
            chips = [{"key": (c.get("name") or c["title"]).lower(), "title": c["title"],
                      "memory_mb": c.get("memory_mb", 0), "exe_path": c.get("exe_path"),
                      "category_label": "Закрито PulseFPS", "note": "Після вимкнення режиму запропоную відкрити знову"}
                     for c in closed]
            self.chips.set_chips(chips, False, "Режим не закривав жодних програм.")
            theme.set_text(self.apps_title, "Закрито PulseFPS")
            theme.set_text(self.apps_info, f"звільнено {_fmt_freed(self.state.get('freed_mb', 0))} RAM"
                           if self.state.get("freed_mb") else "")
        else:
            summary = self._idle_summary(apps, extras)
            chips = [{"key": a["key"], "title": a["title"], "memory_mb": a["memory_mb"],
                      "exe_path": a["exe_path"], "count": a["count"],
                      "category_label": smart_apps.CATEGORY_LABELS[a["category"]]} for a in apps]
            chips += [{"key": "proc:" + p, "title": p, "memory_mb": self._running_mem.get(p.lower(), 0),
                       "exe_path": None, "icon": False, "category_label": "Позначено вручну (профіль)"}
                      for p in extras]
            self.chips.set_chips(chips, True, "Фонових програм для закриття не знайдено — усе вже чисто.")
            theme.set_text(self.apps_title, "Фонові програми для закриття")
            total = sum(c["memory_mb"] for c in chips)
            theme.set_text(self.apps_info, f"{len(chips)} шт. · {fmt_mem(total)} RAM" if chips else "")
        theme.set_text(self.summary_label, summary)

        note = self._note
        if not note and not active and self._plan_value() == game_mode_core.ULTRA and self._on_battery:
            note = ("Ноутбук працює від батареї: «PulseFPS Ultra» швидко її розряджає й автоматично "
                    "не вмикається. Вручну — спитаю підтвердження.")
        theme.set_text(self.note_label, note)

        excluded = self.state.get("excluded_apps", [])
        if excluded and not active:
            theme.set_text(self.restore_button, f"Повернути вилучені програми ({len(excluded)})")
            self.restore_button.grid()
        else:
            self.restore_button.grid_remove()

    def _plan_phrase(self, plan: str) -> str:
        if not plan:
            return "план живлення не змінюватиму"
        return f"увімкну план «{game_mode_core.power_plan_name(plan)}»"

    def _idle_summary(self, apps: list[dict], extras: list[str]) -> str:
        n = len(apps) + len(extras)
        freed = sum(a["memory_mb"] for a in apps) + sum(self._running_mem.get(p.lower(), 0) for p in extras)
        plan = self._plan_phrase(self._plan_value())
        if not n:
            return f"Фонових програм для закриття немає, {plan}."
        text = f"Закрию {n} {_plural(n, 'фонову програму', 'фонові програми', 'фонових програм')}, {plan}"
        return f"{text}, звільню {_fmt_freed(freed)} RAM" if freed >= 10 else text

    def _active_summary(self, closed: int) -> str:
        plan = self.state.get("plan_name")
        parts = [f"Закрито {closed} {_plural(closed, 'програму', 'програми', 'програм')}"
                 if closed else "Програм для закриття не було"]
        freed = self.state.get("freed_mb", 0)
        if freed >= 10:
            parts.append(f"звільнено {_fmt_freed(freed)} RAM")
        parts.append(f"план «{plan}»" if plan else "план живлення не змінено")
        return ", ".join(parts) + "."

    # --------------------------------------------------- чіпи (розумний список)

    def _save(self) -> None:
        with self._lock:
            game_mode_core.save_game_mode(self.state)

    def _on_chip_remove(self, key: str) -> None:
        with self._lock:
            if key.startswith("proc:"):
                processes = self._profile().get("processes", [])
                name = key[5:]
                if name in processes:
                    processes.remove(name)
            elif key not in self.state["excluded_apps"]:
                self.state["excluded_apps"].append(key)
            self._save()
        self._render()

    def _on_restore_excluded(self) -> None:
        with self._lock:
            self.state["excluded_apps"] = []
            self._save()
        self._render()

    # -------------------------------------------------------------- вмикання

    def is_active(self) -> bool:
        return bool(self.state.get("is_active"))

    def enable(self) -> None:
        """Увімкнути режим (для кнопки робота на «Моніторі»)."""
        if not self._busy and not self.is_active():
            self._request_enable()

    def _on_switch_clicked(self) -> None:
        if self._busy:
            return
        if self.is_active():
            self._start_deactivate()
        else:
            self._request_enable()

    def _request_enable(self) -> None:
        plan_override = None
        if self._plan_value() == game_mode_core.ULTRA and power_plans.on_battery():
            answer = messagebox.askyesnocancel(
                "Ноутбук працює від батареї",
                "«PulseFPS Ultra» тримає процесор на максимумі й швидко розряджає батарею.\n\n"
                "Так — усе одно ввімкнути план Ultra\n"
                "Ні — увімкнути режим без зміни плану живлення\n"
                "Скасувати — нічого не робити",
                parent=self,
            )
            if answer is None:
                return
            if answer is False:
                plan_override = ""
        apps, extras = self._current_apps(), self._current_extras()
        if apps or extras:
            names = [a["title"] for a in apps] + extras
            shown = "\n".join("• " + n for n in names[:12]) + (f"\n… і ще {len(names) - 12}" if len(names) > 12 else "")
            if not messagebox.askyesno(
                "Ігровий режим",
                f"Буде закрито:\n{shown}\n\nНезбережені дані в цих програмах можуть загубитись. Продовжити?",
                parent=self,
            ):
                return
        self._busy = True
        self._render()
        threading.Thread(target=self._activate_worker, args=(False, plan_override), daemon=True).start()

    def _activate_worker(self, auto: bool, plan_override: str | None = None) -> None:
        """Довга операція (закриття процесів, powercfg) — у фоні, на копії стану."""
        try:
            with self._lock:
                work = copy.deepcopy(self.state)
            report = game_mode_core.activate(
                work, work["active_profile"], {g["platform"] for g in self._games}, auto=auto,
                plan_override=plan_override,
            )
            self._commit(work)
            self._auto_enabled = auto
        except Exception as exc:
            _logger.exception("Не вдалося ввімкнути ігровий режим")
            report = {"errors": [str(exc)], "plan_error": "", "closed": []}
        self._post(self._on_activated, report)

    def _commit(self, work: dict) -> None:
        with self._lock:
            for key in _COMMIT_KEYS:
                self.state[key] = work[key]
            game_mode_core.save_game_mode(self.state)

    def _on_activated(self, report: dict) -> None:
        self._busy = False
        problems = list(report.get("errors", []))
        if report.get("plan_error"):
            problems.append(report["plan_error"])
        self._note = ("Не все вдалося: " + "; ".join(problems[:3])) if problems else ""
        self._render()

    def _start_deactivate(self, offer_reopen: bool = True) -> None:
        self._busy = True
        self._render()
        threading.Thread(target=self._deactivate_worker, args=(offer_reopen,), daemon=True).start()

    def _deactivate_worker(self, offer_reopen: bool = True) -> None:
        closed = []
        try:
            with self._lock:
                work = copy.deepcopy(self.state)
            closed = game_mode_core.deactivate(work)
            self._commit(work)
        except Exception:
            _logger.exception("Не вдалося вимкнути ігровий режим")
        self._auto_enabled = False
        self._post(self._on_deactivated, closed if offer_reopen else [])

    def _on_deactivated(self, closed: list[dict]) -> None:
        self._busy = False
        self._note = ""
        self._render()
        self._wake.set()
        if closed:
            self._offer_reopen(closed)

    def _offer_reopen(self, closed: list[dict]) -> None:
        names = "\n".join("• " + c["title"] for c in closed[:12])
        if messagebox.askyesno(
            "Відкрити програми знову?",
            f"Під час ігрового режиму PulseFPS закрив:\n{names}\n\nВідкрити їх знову?",
            parent=self,
        ):
            threading.Thread(target=self._reopen_worker, args=(closed,), daemon=True).start()

    def _reopen_worker(self, closed: list[dict]) -> None:
        failed = smart_apps.reopen_apps(closed)
        if failed:
            self._post(self._set_note, "Не вдалося відкрити: " + ", ".join(failed))

    def _set_note(self, note: str) -> None:
        self._note = note
        self._render()

    def _check_crash_recovery(self) -> None:
        """PulseFPS закрився (або впав), поки режим був увімкнений: план живлення міг
        лишитися ігровим — пропонуємо повернути попередній."""
        if not self.state.get("is_active") or self._busy or self._stop_event.is_set():
            return
        if not self.state.get("previous_power_plan"):
            with self._lock:
                self.state["is_active"] = False
                self._save()
            self._render()
            return
        if messagebox.askyesno(
            "Ігровий режим",
            "Минулого разу PulseFPS закрився, поки Ігровий режим був увімкнений, — "
            "план живлення міг лишитися ігровим.\n\nПовернути попередній план живлення?",
            parent=self,
        ):
            self._start_deactivate(offer_reopen=False)

    # ------------------------------------------------------------------ ігри

    def _scan_games(self) -> None:
        try:
            games = game_scanner.scan_games()
        except Exception:
            _logger.exception("Не вдалося знайти ігри")
            games = []
        self._post(self._apply_games, games)

    def _rescan_games(self) -> None:
        theme.set_text(self.games_info, "Шукаю ігри…")
        threading.Thread(target=self._scan_games, daemon=True).start()

    def _apply_games(self, games: list[dict]) -> None:
        self._games = games
        self._refresh_games_list()
        self._wake.set()  # платформи лаунчерів змінилися — оновити список програм

    def _refresh_games_list(self) -> None:
        auto = set(self.state.get("auto_games", []))
        items = [{"key": g["key"], "name": g["name"], "platform": g["platform"], "exe": g["exe"],
                  "folder": g["folder"], "auto": g["key"] in auto, "running": g["key"] in self._running_game_keys}
                 for g in self._games]
        self.games_list.set_empty_text("Ігор Steam, Epic, Riot, Battle.net, EA чи Ubisoft не знайдено")
        self.games_list.set_items(items)
        enabled = sum(1 for i in items if i["auto"])
        theme.set_text(self.games_info, f"{len(items)} знайдено · авто: {enabled}" if items else "")

    def _on_game_toggle(self, key: str, enabled: bool) -> None:
        with self._lock:
            auto = self.state["auto_games"]
            if enabled and key not in auto:
                auto.append(key)
            elif not enabled and key in auto:
                auto.remove(key)
            self._save()
        self._refresh_games_list()

    def _set_all_auto(self, enabled: bool) -> None:
        with self._lock:
            self.state["auto_games"] = [g["key"] for g in self._games] if enabled else []
            self._save()
        self._refresh_games_list()

    # ------------------------------------------------- стеження за іграми

    def _exe_table(self) -> dict:
        """exe (нижній регістр) -> (назва гри, ключ, авто?, усі exe цієї гри)."""
        auto = set(self.state.get("auto_games", []))
        table = {}
        for game in self._games:
            exes = set(game["exe_names"])
            for exe in exes:
                table.setdefault(exe, (game["name"], game["key"], game["key"] in auto, exes))
        for exe in self.state.get("games", []):  # додані вручну: завжди з автовмиканням, як раніше
            low = exe.lower()
            table[low] = (exe[:-4] if low.endswith(".exe") else exe, "manual:" + low, True, {low})
        return table

    def _monitor_snapshot(self):
        tab = self._monitor_tab
        return tab.latest_snapshot() if tab is not None else None

    def _watch_loop(self) -> None:
        tracker = None
        exes: set = set()
        auto_game = False
        missing = 0
        detected_text = None

        while not self._stop_event.is_set():
            try:
                running = game_mode_core.get_running_process_name_set()
                table = self._exe_table()
                if tracker is None:
                    found = next((table[e] for e in running if e in table), None)
                    if found is not None:
                        name, key, auto_game, exes = found
                        tracker = game_sessions.SessionTracker(name, key)
                        missing = 0
                        if auto_game and not self.state.get("is_active") and not self._busy:
                            self._busy = True
                            self._post(self._render)
                            self._activate_worker(auto=True)
                else:
                    if exes & running:
                        missing = 0
                        tracker.add_sample(self._monitor_snapshot())
                    else:
                        missing += 1
                    if missing >= SESSION_END_GRACE_TICKS:
                        self._finish_session(tracker)
                        tracker = None
                        if self._auto_enabled and self.state.get("is_active") and not self._busy:
                            self._busy = True
                            self._post(self._render)
                            self._deactivate_worker()

                keys = {g["key"] for g in self._games if set(g["exe_names"]) & running}
                if keys != self._running_game_keys:
                    self._running_game_keys = keys
                    self._post(self._refresh_games_list)
                text = f"Запущено: {tracker.game}" if tracker else "Ігри не запущені"
                if text != detected_text:
                    detected_text = text
                    self._post(self._update_detect_label, text)
            except Exception:
                _logger.exception("Помилка стеження за іграми")
            self._stop_event.wait(GAME_CHECK_INTERVAL_SEC)

    def _finish_session(self, tracker) -> None:
        summary = tracker.finish()
        if summary is not None:
            game_sessions.add_session(summary)
            self._post(self._on_session_done)

    def _update_detect_label(self, text: str) -> None:
        if self._advanced_built and self.winfo_exists():
            theme.set_text(self.game_detect_label, text)

    # ---------------------------------------------------------------- сесії

    def _on_session_done(self) -> None:
        self._render_sessions()
        try:
            from core import sounds
            sounds.play_success()
        except Exception:
            pass

    def _render_sessions(self) -> None:
        sessions = game_sessions.load_sessions()
        self.sessions_list.set_items(sessions)
        if not sessions:
            self.last_card.grid_remove()
            return
        self.last_card.grid()
        s = sessions[0]
        when = time.strftime("%d.%m.%Y %H:%M", time.localtime(s["started"]))
        theme.set_text(self.last_title, s["game"])
        theme.set_text(self.last_sub, f"{game_sessions.format_duration(s['duration_s'])}  ·  {when}")
        theme.set_text(self._tiles["cpu"], f"{fmt_pct(s.get('cpu_avg'))} / {fmt_pct(s.get('cpu_max'))}")
        theme.set_text(self._tiles["gpu"], f"{fmt_pct(s.get('gpu_avg'))} / {fmt_pct(s.get('gpu_max'))}")
        theme.set_text(self._tiles["cpu_temp"], fmt_temp(s.get("cpu_temp_max")))
        theme.set_text(self._tiles["gpu_temp"], fmt_temp(s.get("gpu_temp_max")))

    def _clear_sessions(self) -> None:
        if not game_sessions.load_sessions():
            return
        if messagebox.askyesno("Історія сесій", "Очистити історію ігрових сесій?", parent=self):
            game_sessions.clear_sessions()
            self._render_sessions()

    # ------------------------------------------------------------ «Розширені»

    def _toggle_advanced(self) -> None:
        if self.adv_body is None:
            self._build_advanced_body()
            self.adv_button.configure(text="▾  Розширені")
            self.after(50, self._scroll_to_advanced)
        elif self.adv_body.winfo_manager():
            self.adv_body.grid_remove()
            self.adv_button.configure(text="▸  Розширені")
        else:
            self.adv_body.grid()
            self.adv_button.configure(text="▾  Розширені")
            self.after(50, self._scroll_to_advanced)

    def _scroll_to_advanced(self) -> None:
        self.page.canvas.yview_moveto(1.0)

    def _build_advanced_body(self) -> None:
        S = self._get_widget_scaling()
        body = self.adv_body = ctk.CTkFrame(self.adv_card, fg_color="transparent")
        body.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="ew")
        body.grid_columnconfigure((0, 1), weight=1, uniform="adv")

        # --- профіль і план живлення
        left = ctk.CTkFrame(body, fg_color="transparent")
        left.grid(row=0, column=0, padx=(0, 10), sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="Профіль", font=theme.font_body()).grid(row=0, column=0, sticky="w")
        self.profile_var = tk.StringVar(value=self.state["active_profile"])
        ctk.CTkOptionMenu(left, variable=self.profile_var, values=list(PROFILE_NAMES),
                          command=self._on_profile_selected).grid(row=1, column=0, pady=(2, 10), sticky="ew")
        ctk.CTkLabel(left, text="План живлення профілю", font=theme.font_body()).grid(row=2, column=0, sticky="w")
        self._plan_choices = game_mode_core.plan_choices()
        self.power_plan_var = tk.StringVar()
        self.power_plan_menu = ctk.CTkOptionMenu(
            left, variable=self.power_plan_var, values=list(self._plan_choices),
            command=self._on_power_plan_selected,
        )
        self.power_plan_menu.grid(row=3, column=0, pady=(2, 12), sticky="ew")
        self._small_button(left, "Видалити план PulseFPS Ultra", self._on_delete_ultra, width=10,
                           fg_color="#a8283f", hover_color=theme.ERROR).grid(row=4, column=0, pady=(0, 6), sticky="ew")
        self._small_button(left, "Повернути звичайний план", self._on_restore_plan, width=10,
                           fg_color=theme.BG_PANEL_LIGHT, hover_color=theme.ACCENT_BLUE_DIM).grid(
            row=5, column=0, pady=(0, 6), sticky="ew")
        self.adv_result = ctk.CTkLabel(left, text="", font=theme.font_small(), text_color=theme.TEXT_DIM,
                                       wraplength=300, justify="left", anchor="w")
        self.adv_result.grid(row=6, column=0, sticky="ew")

        # --- ручний вибір процесів
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, padx=(10, 0), sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(right, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Процеси для закриття (вручну)", font=theme.font_body()).grid(
            row=0, column=0, sticky="w")
        self._small_button(head, "Оновити", self._refresh_process_list, width=72).grid(row=0, column=1)
        self.process_list = ProcessCheckList(right, on_toggle=self._on_process_toggle)
        self.process_list.canvas.configure(width=10, height=round(230 * S))
        self.process_list.grid(row=1, column=0, sticky="nsew")

        # --- ігри вручну
        games = ctk.CTkFrame(body, fg_color="transparent")
        games.grid(row=1, column=0, columnspan=2, pady=(14, 0), sticky="ew")
        games.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(games, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Ігри, додані вручну (.exe) — режим вмикається автоматично",
                     font=theme.font_body()).grid(row=0, column=0, sticky="w")
        self.game_detect_label = ctk.CTkLabel(head, text="Ігри не запущені", font=theme.font_small(),
                                              text_color=theme.TEXT_DIM)
        self.game_detect_label.grid(row=0, column=1, sticky="e")
        entry_row = ctk.CTkFrame(games, fg_color="transparent")
        entry_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        entry_row.grid_columnconfigure(0, weight=1)
        self.game_entry = ctk.CTkEntry(entry_row, placeholder_text="назва.exe")
        self.game_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._small_button(entry_row, "Огляд...", self._browse_game).grid(row=0, column=1, padx=(0, 6))
        self._small_button(entry_row, "Додати", self._add_game).grid(row=0, column=2)
        list_row = ctk.CTkFrame(games, fg_color="transparent")
        list_row.grid(row=2, column=0, sticky="ew")
        list_row.grid_columnconfigure(0, weight=1)
        self.games_listbox = tk.Listbox(
            list_row, height=4, bg=theme.BG_MAIN, fg=theme.TEXT_MAIN, selectbackground=theme.ACCENT_BLUE_DIM,
            highlightthickness=0, borderwidth=0,
        )
        self.games_listbox.grid(row=0, column=0, sticky="ew")
        self._small_button(list_row, "Видалити", self._remove_selected_game, width=90,
                           fg_color="#a8283f", hover_color=theme.ERROR).grid(row=0, column=1, padx=(8, 0), sticky="n")

        self._advanced_built = True
        self._load_profile_into_ui()
        self._refresh_process_list()
        self._refresh_games_listbox()

    def _load_profile_into_ui(self) -> None:
        plan = self._plan_value()
        self.power_plan_var.set(game_mode_core.power_plan_name(plan))

    def _on_profile_selected(self, value: str) -> None:
        with self._lock:
            self.state["active_profile"] = value
            self._save()
        self._load_profile_into_ui()
        self._refresh_process_list()
        self._render()

    def _on_power_plan_selected(self, value: str) -> None:
        with self._lock:
            self._profile()["power_plan"] = self._plan_choices.get(value, "")
            self._save()
        self._render()

    def _set_adv_result(self, text: str, error: bool = False) -> None:
        if self._advanced_built:
            theme.set_text(self.adv_result, text, text_color=theme.ERROR if error else theme.TEXT_DIM)

    def _on_delete_ultra(self) -> None:
        if not messagebox.askyesno(
            "План PulseFPS Ultra",
            "Видалити план живлення «PulseFPS Ultra» з Windows?\n\nПрофілі, що його використовували, "
            "перейдуть на «Високу продуктивність».", parent=self,
        ):
            return
        threading.Thread(target=self._delete_ultra_worker, daemon=True).start()

    def _delete_ultra_worker(self) -> None:
        guid = self.state.get("ultra_guid")
        ok, message = True, ""
        if guid and power_plans.scheme_exists(guid):
            ok, message = power_plans.delete_scheme(guid)
        if ok:
            with self._lock:
                game_mode_core.drop_ultra_from_profiles(self.state)
                game_mode_core.save_game_mode(self.state)
        self._post(self._on_ultra_deleted, ok, message)

    def _on_ultra_deleted(self, ok: bool, message: str) -> None:
        self._set_adv_result("План «PulseFPS Ultra» видалено." if ok else f"Не вдалося видалити: {message}", not ok)
        self._load_profile_into_ui()
        self._render()

    def _on_restore_plan(self) -> None:
        if self.is_active():
            self._start_deactivate()  # вимкнення режиму саме повертає попередній план
            self._set_adv_result("Режим вимкнено, попередній план повернуто.")
            return
        threading.Thread(target=self._restore_plan_worker, daemon=True).start()

    def _restore_plan_worker(self) -> None:
        target = game_mode_core.normal_plan_target(self.state.get("previous_power_plan"))
        ok, message = power_plans.set_active_scheme(target)
        if ok:
            with self._lock:
                self.state["previous_power_plan"] = None
                game_mode_core.save_game_mode(self.state)
        self._post(self._set_adv_result,
                   "Повернуто план «Збалансований»." if ok and target == power_plans.BALANCED_GUID
                   else ("Попередній план повернуто." if ok else f"Не вдалося: {message}"), not ok)

    # --- ручні процеси профілю

    def _refresh_process_list(self) -> None:
        selected = set(self._profile().get("processes", []))
        items = []
        for proc in game_mode_core.get_running_process_names():
            name = proc["name"]
            label = name if proc["count"] <= 1 else f"{name} ({proc['count']})"
            items.append({
                "name": name, "label": label, "protected": proc["protected"], "pid": proc.get("pid"),
                "checked": name in selected and not proc["protected"],
            })
        self.process_list.set_items(items)

    def _on_process_toggle(self, name: str, checked: bool) -> None:
        with self._lock:
            processes = self._profile().setdefault("processes", [])
            if checked and name not in processes:
                processes.append(name)
            elif not checked and name in processes:
                processes.remove(name)
            self._save()
        self._render()

    # --- ігри вручну

    def _browse_game(self) -> None:
        path = filedialog.askopenfilename(
            title="Виберіть виконуваний файл гри",
            filetypes=[("Виконувані файли", "*.exe"), ("Усі файли", "*.*")],
            parent=self,
        )
        if not path:
            return
        self.game_entry.delete(0, "end")
        self.game_entry.insert(0, os.path.basename(path))

    def _add_game(self) -> None:
        name = self.game_entry.get().strip()
        if not name:
            return
        if not name.lower().endswith(".exe"):
            name += ".exe"
        if name.lower() in {g.lower() for g in self.state["games"]}:
            messagebox.showinfo("Інфо", f"«{name}» вже є у списку.", parent=self)
            return
        with self._lock:
            self.state["games"].append(name)
            self._save()
        self.game_entry.delete(0, "end")
        self._refresh_games_listbox()

    def _remove_selected_game(self) -> None:
        selection = self.games_listbox.curselection()
        if not selection:
            return
        with self._lock:
            index = selection[0]
            if 0 <= index < len(self.state["games"]):
                del self.state["games"][index]
                self._save()
        self._refresh_games_listbox()

    def _refresh_games_listbox(self) -> None:
        self.games_listbox.delete(0, "end")
        for name in self.state["games"]:
            self.games_listbox.insert("end", name)
