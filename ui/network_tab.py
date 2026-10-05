"""Вкладка «Мережа»: тест якості з'єднання для ігор (30 с) + перемикач
«Живий режим» для безперервного моніторингу пінгу до заданих хостів."""

import time
import tkinter as tk
from tkinter import messagebox
from collections import deque
from datetime import datetime

import customtkinter as ctk

from core import network as network_core
from core import settings as app_settings
from ui import bg, theme
from ui.widgets.cleaner_bot import CleanerBotAnimation
from core.i18n import TDict, t

GRAPH_POINTS = 60

# Тексти вкладки — ключі перекладів (locales/*.json): показуються через t().
TXT_TEST_INTRO = "network.intro"
TXT_TEST_HINT = "network.intro_hint"
TXT_HELP_SHOW = "network.help_show"
TXT_HELP_HIDE = "network.help_hide"
HELP_CARDS = (
    ("📡", "network.help.ping.title", "network.help.ping.text", "network.help.ping.norm"),
    ("〰️", "network.help.jitter.title", "network.help.jitter.text", "network.help.jitter.norm"),
    ("📦", "network.help.loss.title", "network.help.loss.text", "network.help.loss.norm"),
)
COLUMN_TIPS = TDict({
    "network.col.date": "network.col.date.tip",
    "network.col.ping": "network.col.ping.tip",
    "network.col.jitter": "network.col.jitter.tip",
    "network.col.loss": "network.col.loss.tip",
    "network.col.rating": "network.col.rating.tip",
})
SETTING_HELP_COLLAPSED = "network_help_collapsed"
TXT_HISTORY_TITLE = "network.history.title"
TXT_HISTORY_EMPTY = "network.history.empty"
TXT_CLEAR_HISTORY = "settings.data.clear_history"
TXT_CLEAR_CONFIRM_TITLE = "settings.data.clear_history"
TXT_CLEAR_CONFIRM = "network.history.clear_confirm"
TXT_ENTRY_DELETED = "network.history.deleted"
TXT_UNDO = "common.undo"
UNDO_TIMEOUT_MS = 5000


class Tooltip:
    """Проста підказка при наведенні на віджет."""

    def __init__(self, widget, text: str, delay_ms: int = 400):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self):
        self._after_id = None
        if not self.text or self._tip is not None:
            return
        try:
            x = self.widget.winfo_pointerx() + 12
            y = self.widget.winfo_pointery() + 16
            self._tip = tk.Toplevel(self.widget)
            self._tip.wm_overrideredirect(True)
            self._tip.wm_geometry(f"+{x}+{y}")
            tk.Label(
                self._tip, text=self.text, justify="left", wraplength=280, bg="#2b2b2b", fg="#e6e6e6",
                relief="solid", borderwidth=1, padx=8, pady=5, font=(theme.font_family(), 9),
            ).pack()
        except tk.TclError:
            self._tip = None

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


class PingGraph(ctk.CTkFrame):
    """Лінійний графік історії пінгу (мс) з автомасштабуванням і розривами при втратах."""

    def __init__(self, master, color: str, height: int = 70, points: int = GRAPH_POINTS):
        super().__init__(master, fg_color="transparent")
        self.color = color
        self.points = points
        self.history = deque([None] * points, maxlen=points)

        self.canvas = tk.Canvas(self, height=height, bg="#1a1a1a", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self._dirty = False
        self.canvas.bind("<Configure>", lambda _e: self._redraw())
        # прихована вкладка лише накопичує історію; перемальовуємо при показі
        self.canvas.bind("<Map>", lambda _e: self._dirty and self._redraw())

    def push(self, value: float | None) -> None:
        self.history.append(value)
        self._redraw()

    def clear(self) -> None:
        self.history = deque([None] * self.points, maxlen=self.points)
        self._redraw()

    def _redraw(self) -> None:
        if not self.canvas.winfo_ismapped():
            self._dirty = True
            return
        self._dirty = False
        self.canvas.delete("all")
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width <= 1 or height <= 1:
            return

        values = [v for v in self.history if v is not None]
        max_value = max(values) * 1.2 if values else 50.0
        max_value = max(max_value, 20.0)

        n = len(self.history)
        step = width / (n - 1) if n > 1 else width

        segment = []
        for i, value in enumerate(self.history):
            x = i * step
            if value is None:
                if len(segment) >= 4:
                    self.canvas.create_line(*segment, fill=self.color, width=2, smooth=True)
                segment = []
                continue
            y = height - (value / max_value) * height
            segment.extend((x, y))

        if len(segment) >= 4:
            self.canvas.create_line(*segment, fill=self.color, width=2, smooth=True)


class PingCard(ctk.CTkFrame):
    """Картка одного хоста: назва, поточний пінг, графік і зведена статистика."""

    def __init__(self, master, title: str, color: str):
        super().__init__(master, corner_radius=10)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 4))

        self.title_label = ctk.CTkLabel(header, text=title, font=ctk.CTkFont(size=14, weight="bold"))
        self.title_label.pack(side="left")

        self.value_label = ctk.CTkLabel(header, text="—", font=ctk.CTkFont(size=14, weight="bold"))
        self.value_label.pack(side="right")

        self.graph = PingGraph(self, color=color)
        self.graph.pack(fill="x", padx=12, pady=(0, 6))

        self.stats_label = ctk.CTkLabel(
            self,
            text=t("network.card.empty"),
            text_color="gray",
            font=ctk.CTkFont(size=11),
            anchor="w",
            justify="left",
        )
        self.stats_label.pack(fill="x", padx=12, pady=(0, 10), anchor="w")

    def set_title(self, title: str) -> None:
        self.title_label.configure(text=title)

    def apply(self, latency: float | None, stats: dict) -> None:
        if latency is None:
            theme.set_text(self.value_label, t("network.timeout"), text_color="#ff5c7a")
        else:
            theme.set_text(self.value_label, t("units.ms", v=latency), text_color=("gray10", "gray90"))
        self.graph.push(latency)

        avg = t("units.ms", v=stats['avg']) if stats["avg"] is not None else "—"
        jitter = t("units.ms", v=stats['jitter']) if stats["jitter"] is not None else "—"
        loss = stats["loss_percent"]
        loss_text = f"{loss:.0f}%"
        loss_color = "#ff5c7a" if loss > 0 else "gray"

        theme.set_text(
            self.stats_label, t("network.card.stats", avg=avg, jitter=jitter, loss_text=loss_text),
            text_color=loss_color,
        )

    def reset(self, idle_text: str = t("network.card.empty")) -> None:
        theme.set_text(self.value_label, "—", text_color=("gray10", "gray90"))
        theme.set_text(self.stats_label, idle_text, text_color="gray")
        self.graph.clear()


class TestHistoryTable(ctk.CTkFrame):
    """Таблиця останніх тестів: дата, пінг, джитер, втрати, оцінка.

    Заголовки й рядки лежать в одній grid-сітці з однаковими колонками, тож
    значення стоять точно під заголовками. Праворуч від рядка при наведенні
    з'являється іконка кошика (on_delete отримує індекс запису)."""

    COLUMNS = ("network.col.date", "network.col.ping", "network.col.jitter", "network.col.loss", "network.col.rating")
    TRASH_COL = len(COLUMNS)

    def __init__(self, master, on_delete=None):
        super().__init__(master, corner_radius=10)
        self._on_delete = on_delete
        self._rows: list[dict] = []

        self.grid_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.grid_frame.pack(fill="x", padx=12, pady=(10, 10))
        for i in range(len(self.COLUMNS)):
            self.grid_frame.grid_columnconfigure(i, weight=1, uniform="hist")
        self.grid_frame.grid_columnconfigure(self.TRASH_COL, weight=0, minsize=28)

        self._header_labels = []
        for i, title in enumerate(self.COLUMNS):
            head = ctk.CTkLabel(
                self.grid_frame, text=t(title), font=ctk.CTkFont(size=11, weight="bold"),
                text_color="gray", anchor="w",
            )
            head.grid(row=0, column=i, sticky="ew", pady=(0, 4))
            Tooltip(head, COLUMN_TIPS.get(title, ""))
            self._header_labels.append(head)

        self.empty_frame = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkLabel(self.empty_frame, text="🤖", font=ctk.CTkFont(size=34)).pack(pady=(14, 2))
        ctk.CTkLabel(
            self.empty_frame, text=t(TXT_HISTORY_EMPTY), text_color="gray",
            font=ctk.CTkFont(size=12), wraplength=420, justify="center",
        ).pack(padx=16, pady=(0, 16))

    def set_data(self, entries: list) -> None:
        for row in self._rows:
            for widget in row["widgets"]:
                widget.destroy()
            if row["after"] is not None:
                try:
                    self.after_cancel(row["after"])
                except tk.TclError:
                    pass
        self._rows.clear()

        if not entries:
            self.grid_frame.pack_forget()
            self.empty_frame.pack(fill="x")
            return
        self.empty_frame.pack_forget()
        self.grid_frame.pack(fill="x", padx=12, pady=(10, 10))

        for r, entry in enumerate(entries):
            avg = entry.get("avg")
            jitter = entry.get("jitter")
            loss = entry.get("loss", 0) or 0
            values = (
                entry.get("date", "—"),
                t("units.ms", v=avg) if avg is not None else "—",
                t("units.ms", v=jitter) if jitter is not None else "—",
                f"{loss:.0f}%",
                network_core.rating_label(network_core.entry_level(entry)),
            )
            row = {"widgets": [], "hover": False, "after": None}
            for c, value in enumerate(values):
                color = network_core.RATING_COLORS[network_core.entry_level(entry)] if c == 4 else None
                lbl = ctk.CTkLabel(
                    self.grid_frame, text=str(value), font=ctk.CTkFont(size=11),
                    text_color=color or ("gray10", "gray90"), anchor="w",
                )
                lbl.grid(row=r + 1, column=c, sticky="ew", pady=2)
                if c == 4:
                    Tooltip(lbl, network_core.explain_entry_rating(avg, jitter, loss))
                row["widgets"].append(lbl)

            trash = ctk.CTkLabel(
                self.grid_frame, text="", width=24, cursor="hand2",
                font=ctk.CTkFont(size=13), text_color="#ff5c7a",
            )
            trash.grid(row=r + 1, column=self.TRASH_COL, sticky="e", pady=2)
            trash.bind("<Button-1>", lambda _e, i=r: self._delete(i))
            row["widgets"].append(trash)
            row["trash"] = trash

            for widget in row["widgets"]:
                widget.bind("<Enter>", lambda _e, rw=row: self._set_hover(rw, True), add="+")
                widget.bind("<Leave>", lambda _e, rw=row: self._leave(rw), add="+")
            self._rows.append(row)

    def _set_hover(self, row: dict, hover: bool) -> None:
        row["hover"] = hover
        if row["after"] is not None:
            try:
                self.after_cancel(row["after"])
            except tk.TclError:
                pass
            row["after"] = None
        try:
            row["trash"].configure(text="🗑" if hover else "")
        except tk.TclError:
            pass

    def _leave(self, row: dict) -> None:
        # Курсор може просто перейти на сусідній віджет цього ж рядка —
        # вирішуємо після короткої паузи, щоб кошик не блимав.
        row["hover"] = False
        row["after"] = self.after(40, lambda: self._finish_leave(row))

    def _finish_leave(self, row: dict) -> None:
        row["after"] = None
        if not row["hover"]:
            self._set_hover(row, False)

    def _delete(self, index: int) -> None:
        if self._on_delete is not None:
            self._on_delete(index)


class NetworkTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self._live_workers = []

        self._test_workers = []
        self._test_after_id = None
        self._test_started_at = 0.0
        self._last_result_text = ""
        self._undo_bar = None
        self._undo_after_id = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_content()

        self.bind("<Destroy>", self._on_destroy)

    # ------------------------------------------------------------------ UI

    def _build_header(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        row.grid_columnconfigure(0, weight=1)

        label = ctk.CTkLabel(row, text=t("tabs.network"), font=ctk.CTkFont(size=22, weight="bold"))
        label.grid(row=0, column=0, sticky="w")

        self.live_switch = ctk.CTkSwitch(row, text=t("network.live"), command=self._on_toggle_live)
        self.live_switch.grid(row=0, column=1, sticky="e")

    def _build_content(self):
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="nsew")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.test_frame = ctk.CTkFrame(container, fg_color="transparent")
        self.test_frame.grid(row=0, column=0, sticky="nsew")

        self.live_frame = ctk.CTkFrame(container, fg_color="transparent")
        self.live_frame.grid(row=0, column=0, sticky="nsew")

        self._build_test_view()
        self._build_live_view()

        self.test_frame.tkraise()

    def _on_toggle_live(self):
        if self.live_switch.get():
            self.live_frame.tkraise()
        else:
            self.test_frame.tkraise()

    # ------------------------------------------------------------ test UI

    def _build_test_view(self):
        self.start_section = ctk.CTkFrame(self.test_frame, fg_color="transparent")
        self.start_section.pack(fill="x", pady=(10, 16))

        ctk.CTkLabel(
            self.start_section, text=t(TXT_TEST_INTRO), wraplength=560, justify="center",
            font=ctk.CTkFont(size=13),
        ).pack(pady=(0, 12))

        self.test_custom_entry = ctk.CTkEntry(
            self.start_section, placeholder_text=t("network.custom_placeholder"), width=280,
        )
        self.test_custom_entry.pack(pady=(0, 14))

        self.start_button = ctk.CTkButton(
            self.start_section, text=t("network.start"), width=260, height=44,
            font=ctk.CTkFont(size=16, weight="bold"), command=self._start_test,
        )
        self.start_button.pack()

        ctk.CTkLabel(
            self.start_section, text=t(TXT_TEST_HINT), text_color="gray", font=ctk.CTkFont(size=10),
        ).pack(pady=(6, 0))

        self._build_help_section(self.start_section)

        self.bot = CleanerBotAnimation(self.test_frame, height=170)

        self.live_cards_frame = ctk.CTkFrame(self.test_frame, fg_color="transparent")
        self.live_cards_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.test_google_card = PingCard(self.live_cards_frame, "Google DNS (8.8.8.8)", color="#4fc3ff")
        self.test_google_card.grid(row=0, column=0, padx=6, pady=6, sticky="nsew")

        self.test_cloudflare_card = PingCard(self.live_cards_frame, "Cloudflare (1.1.1.1)", color="#2ee59d")
        self.test_cloudflare_card.grid(row=0, column=1, padx=6, pady=6, sticky="nsew")

        self.test_custom_card = PingCard(self.live_cards_frame, t("network.custom"), color="#c77dff")
        self.test_custom_card.grid(row=0, column=2, padx=6, pady=6, sticky="nsew")

        self._build_result_section()

        history_wrap = ctk.CTkFrame(self.test_frame, fg_color="transparent")
        history_wrap.pack(fill="x", pady=(6, 0))

        history_head = ctk.CTkFrame(history_wrap, fg_color="transparent")
        history_head.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(
            history_head, text=t(TXT_HISTORY_TITLE), font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")
        self.clear_history_button = ctk.CTkButton(
            history_head, text=t(TXT_CLEAR_HISTORY), width=130, height=26,
            fg_color="transparent", border_width=1, text_color=("gray20", "gray80"),
            command=self._clear_history,
        )
        self.clear_history_button.pack(side="right")

        self.history_table = TestHistoryTable(history_wrap, on_delete=self._delete_history_entry)
        self.history_table.pack(fill="x")
        self._show_history(network_core.load_test_history())

    # ---------------------------------------------------------- history

    def reload_history(self) -> None:
        """Перечитати історію тестів (напр. після «Налаштування → Очистити історію»)."""
        self._show_history(network_core.load_test_history())

    def _show_history(self, history: list) -> None:
        self.history_table.set_data(history)
        self.clear_history_button.configure(state="normal" if history else "disabled")

    def _clear_history(self):
        if not network_core.load_test_history():
            return
        if not messagebox.askyesno(t(TXT_CLEAR_CONFIRM_TITLE), t(TXT_CLEAR_CONFIRM), parent=self):
            return
        self._hide_undo()
        self._show_history(network_core.clear_test_history())

    def _delete_history_entry(self, index: int):
        removed = network_core.delete_test_result(index)
        if removed is None:
            return
        self._show_history(network_core.load_test_history())
        self._show_undo(index, removed)

    def _show_undo(self, index: int, entry: dict):
        self._hide_undo()
        self._undo_bar = ctk.CTkFrame(self, corner_radius=10, border_width=1)
        ctk.CTkLabel(self._undo_bar, text=t(TXT_ENTRY_DELETED)).pack(side="left", padx=(14, 10), pady=8)
        ctk.CTkButton(
            self._undo_bar, text=t(TXT_UNDO), width=90, height=26,
            command=lambda: self._undo_delete(index, entry),
        ).pack(side="left", padx=(0, 12), pady=8)
        self._undo_bar.place(relx=0.5, rely=1.0, y=-14, anchor="s")
        self._undo_after_id = self.after(UNDO_TIMEOUT_MS, self._hide_undo)

    def _undo_delete(self, index: int, entry: dict):
        self._hide_undo()
        self._show_history(network_core.restore_test_result(index, entry))

    def _hide_undo(self):
        if self._undo_after_id is not None:
            try:
                self.after_cancel(self._undo_after_id)
            except tk.TclError:
                pass
            self._undo_after_id = None
        if self._undo_bar is not None:
            self._undo_bar.destroy()
            self._undo_bar = None

    def _build_help_section(self, parent):
        self._help_collapsed = bool(app_settings.load_settings().get(SETTING_HELP_COLLAPSED, False))

        self.help_toggle = ctk.CTkButton(
            parent, text="", width=160, height=24, fg_color="transparent",
            border_width=1, text_color=("gray20", "gray80"), command=self._toggle_help,
        )
        self.help_toggle.pack(pady=(14, 6))

        self.help_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.help_frame.grid_columnconfigure((0, 1, 2), weight=1, uniform="help")
        for i, (icon, title, text, norm) in enumerate(HELP_CARDS):
            card = ctk.CTkFrame(self.help_frame, corner_radius=8)
            card.grid(row=0, column=i, padx=4, sticky="nsew")
            ctk.CTkLabel(
                card, text=f"{icon} {t(title)}", font=ctk.CTkFont(size=12, weight="bold"),
            ).pack(anchor="w", padx=10, pady=(8, 0))
            ctk.CTkLabel(
                card, text=t(text), font=ctk.CTkFont(size=11), wraplength=170, justify="left", anchor="w",
            ).pack(anchor="w", padx=10)
            ctk.CTkLabel(
                card, text=t(norm), font=ctk.CTkFont(size=11), text_color="#2ee59d",
                wraplength=170, justify="left", anchor="w",
            ).pack(anchor="w", padx=10, pady=(0, 8))
        self._apply_help_state()

    def _apply_help_state(self):
        if self._help_collapsed:
            self.help_frame.pack_forget()
            self.help_toggle.configure(text=t(TXT_HELP_SHOW))
        else:
            self.help_frame.pack(fill="x", padx=10)
            self.help_toggle.configure(text=t(TXT_HELP_HIDE))

    def _toggle_help(self):
        self._help_collapsed = not self._help_collapsed
        self._apply_help_state()
        try:
            app_settings.update_setting(SETTING_HELP_COLLAPSED, self._help_collapsed)
        except OSError:
            pass

    def _build_result_section(self):
        self.result_frame = ctk.CTkFrame(self.test_frame, corner_radius=10)

        top = ctk.CTkFrame(self.result_frame, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(16, 6))

        self.result_badge = ctk.CTkLabel(
            top, text="", font=ctk.CTkFont(size=16, weight="bold"), corner_radius=8, width=110, height=30,
        )
        self.result_badge.pack(side="left")

        self.result_stats_label = ctk.CTkLabel(
            self.result_frame, text="", font=ctk.CTkFont(size=13), justify="left", anchor="w",
        )
        self.result_stats_label.pack(fill="x", padx=16, pady=(0, 8), anchor="w")

        self.result_notes_label = ctk.CTkLabel(
            self.result_frame, text="", font=ctk.CTkFont(size=12), justify="left",
            anchor="w", text_color="gray",
        )
        self.result_notes_label.pack(fill="x", padx=16, pady=(0, 12), anchor="w")

        buttons = ctk.CTkFrame(self.result_frame, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(0, 16))

        self.repeat_button = ctk.CTkButton(
            buttons, text=t("network.retry"), width=160, command=self._start_test,
        )
        self.repeat_button.pack(side="left", padx=(0, 8))

        self.copy_button = ctk.CTkButton(
            buttons, text=t("network.copy"), width=180, command=self._copy_result,
        )
        self.copy_button.pack(side="left")

    # -------------------------------------------------------- test control

    def _start_test(self):
        if self._test_workers:
            return

        self.start_section.pack_forget()
        self.result_frame.pack_forget()

        self.bot.pack(fill="x", pady=(0, 16))
        self.live_cards_frame.pack(fill="x", pady=(0, 16))

        self.test_google_card.reset()
        self.test_cloudflare_card.reset()

        targets = [
            (self.test_google_card, "8.8.8.8"),
            (self.test_cloudflare_card, "1.1.1.1"),
        ]

        custom_host = self.test_custom_entry.get().strip()
        if custom_host:
            self.test_custom_card.set_title(t("network.custom_with", host=custom_host))
            self.test_custom_card.reset()
            targets.append((self.test_custom_card, custom_host))
        else:
            self.test_custom_card.set_title(t("network.custom"))
            self.test_custom_card.reset(idle_text=t("network.no_address"))

        self._test_workers = []
        for card, host in targets:
            worker = network_core.PingWorker(
                host,
                on_update=self._make_test_callback(card),
                interval_sec=network_core.TEST_INTERVAL_SEC,
                history_size=80,
            )
            self._test_workers.append(worker)
            worker.start()

        self._test_started_at = time.monotonic()
        self.bot.start(t("network.measuring_30"), tool="scan")
        self._tick_test()

    def _tick_test(self):
        if not self.winfo_exists():
            return

        elapsed = time.monotonic() - self._test_started_at
        remaining = max(0.0, network_core.TEST_DURATION_SEC - elapsed)
        progress = min(1.0, elapsed / network_core.TEST_DURATION_SEC)
        self.bot.update(t("network.measuring", remaining=remaining), progress)

        if elapsed >= network_core.TEST_DURATION_SEC:
            self._finish_test()
            return
        self._test_after_id = self.after(200, self._tick_test)

    def _finish_test(self):
        self._test_after_id = None
        workers, self._test_workers = self._test_workers, []
        for worker in workers:
            worker.stop()

        histories = [worker.history for worker in workers]
        stats = network_core.aggregate_stats(histories)
        rating = network_core.rate_test(stats)

        date_text = datetime.now().strftime("%d.%m %H:%M")
        entry = {
            "date": date_text,
            "avg": stats["avg"],
            "jitter": stats["jitter"],
            "loss": stats["loss_percent"],
            "level": rating["level"],
        }
        history = network_core.save_test_result(entry)
        self._show_history(history)

        success = rating["level"] <= 1
        self.bot.finish(t("network.done") if success else t("network.meh"), success=success)

        self._last_result_text = self._build_result_text(stats, rating, date_text)
        self._render_result(stats, rating)

    def _make_test_callback(self, card: PingCard):
        def callback(host, latency, stats):
            bg.ui_call(self, self._apply_test_update, card, latency, stats)
        return callback

    def _apply_test_update(self, card: PingCard, latency, stats):
        if not self.winfo_exists():
            return
        card.apply(latency, stats)

    def _render_result(self, stats: dict, rating: dict) -> None:
        self.result_badge.configure(text=rating["label"], fg_color=rating["color"], text_color="white")

        def fmt(v):
            return t("units.ms", v=v) if v is not None else "—"

        stats_text = (
            t("network.result.stats", avg=fmt(stats['avg']), min=fmt(stats['min']), max=fmt(stats['max']), jitter=fmt(stats['jitter']), loss_percent=stats['loss_percent'])
        )
        self.result_stats_label.configure(text=stats_text)
        self.result_notes_label.configure(text="\n".join(f"•  {n}" for n in rating["notes"]))

        self.result_frame.pack(fill="x", pady=(0, 16))

    def _build_result_text(self, stats: dict, rating: dict, date_text: str) -> str:
        def fmt(v):
            return t("units.ms", v=v) if v is not None else "—"

        lines = [
            t("network.result.title", date=date_text),
            t("network.result.rating", label=rating['label']),
            t("network.result.ping", avg=fmt(stats['avg']), min=fmt(stats['min']), max=fmt(stats['max'])),
            t("network.result.jitter", jitter=fmt(stats['jitter'])),
            t("network.result.loss", loss_percent=stats['loss_percent']),
        ]
        if rating["notes"]:
            lines.append(t("network.result.conclusions"))
            lines.extend(f"- {n}" for n in rating["notes"])
        return "\n".join(lines)

    def _copy_result(self):
        if not self._last_result_text:
            return
        self.clipboard_clear()
        self.clipboard_append(self._last_result_text)

    # ------------------------------------------------------------ live UI

    def _build_live_view(self):
        controls = ctk.CTkFrame(self.live_frame, fg_color="transparent")
        controls.pack(fill="x", pady=(10, 10))

        self.live_custom_entry = ctk.CTkEntry(
            controls, placeholder_text=t("network.custom_placeholder"), width=260,
        )
        self.live_custom_entry.pack(side="left", padx=(0, 10))

        self.live_start_button = ctk.CTkButton(
            controls, text=t("network.live_start"), width=90, command=self._start_live,
        )
        self.live_start_button.pack(side="left", padx=(0, 6))

        self.live_stop_button = ctk.CTkButton(
            controls, text=t("network.live_stop"), width=90, state="disabled",
            fg_color="#a8283f", hover_color="#ff5c7a", command=self._stop_live,
        )
        self.live_stop_button.pack(side="left")

        cards_frame = ctk.CTkFrame(self.live_frame, fg_color="transparent")
        cards_frame.pack(fill="both", expand=True)
        cards_frame.grid_columnconfigure((0, 1, 2), weight=1)
        cards_frame.grid_rowconfigure(0, weight=1)

        self.live_google_card = PingCard(cards_frame, "Google DNS (8.8.8.8)", color="#4fc3ff")
        self.live_google_card.grid(row=0, column=0, padx=6, pady=6, sticky="nsew")

        self.live_cloudflare_card = PingCard(cards_frame, "Cloudflare (1.1.1.1)", color="#2ee59d")
        self.live_cloudflare_card.grid(row=0, column=1, padx=6, pady=6, sticky="nsew")

        self.live_custom_card = PingCard(cards_frame, t("network.custom"), color="#c77dff")
        self.live_custom_card.grid(row=0, column=2, padx=6, pady=6, sticky="nsew")

    def _start_live(self):
        if self._live_workers:
            return

        self.live_google_card.reset()
        self.live_cloudflare_card.reset()

        targets = [
            (self.live_google_card, "8.8.8.8"),
            (self.live_cloudflare_card, "1.1.1.1"),
        ]

        custom_host = self.live_custom_entry.get().strip()
        if custom_host:
            self.live_custom_card.set_title(t("network.custom_with", host=custom_host))
            self.live_custom_card.reset()
            targets.append((self.live_custom_card, custom_host))
        else:
            self.live_custom_card.set_title(t("network.custom"))
            self.live_custom_card.reset(idle_text=t("network.no_address"))

        for card, host in targets:
            worker = network_core.PingWorker(host, on_update=self._make_live_callback(card))
            self._live_workers.append(worker)
            worker.start()

        self.live_start_button.configure(state="disabled")
        self.live_stop_button.configure(state="normal")
        self.live_custom_entry.configure(state="disabled")

    def _stop_live(self):
        for worker in self._live_workers:
            worker.stop()
        self._live_workers.clear()

        self.live_start_button.configure(state="normal")
        self.live_stop_button.configure(state="disabled")
        self.live_custom_entry.configure(state="normal")

    def _make_live_callback(self, card: PingCard):
        def callback(host, latency, stats):
            bg.ui_call(self, self._apply_live_update, card, latency, stats)
        return callback

    def _apply_live_update(self, card: PingCard, latency, stats):
        if not self.winfo_exists():
            return
        card.apply(latency, stats)

    # -------------------------------------------------------------- misc

    def _on_destroy(self, event):
        if event.widget not in (self, getattr(self, "_canvas", None)):  # CTkFrame.bind вішається на внутрішній canvas
            return

        if self._undo_after_id is not None:
            try:
                self.after_cancel(self._undo_after_id)
            except tk.TclError:
                pass
            self._undo_after_id = None

        if self._test_after_id is not None:
            try:
                self.after_cancel(self._test_after_id)
            except tk.TclError:
                pass
            self._test_after_id = None

        for worker in self._test_workers:
            worker.stop()
        self._test_workers.clear()

        for worker in self._live_workers:
            worker.stop()
        self._live_workers.clear()

    def is_busy(self) -> bool:
        """Триває операція, яку не можна перервати перебудовою вкладки (зміна мови)."""
        return bool(self._test_workers)
