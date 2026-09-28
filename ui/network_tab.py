"""Вкладка «Мережа»: тест якості з'єднання для ігор (30 с) + перемикач
«Живий режим» для безперервного моніторингу пінгу до заданих хостів."""

import time
import tkinter as tk
from collections import deque
from datetime import datetime

import customtkinter as ctk

from core import network as network_core
from ui.widgets.cleaner_bot import CleanerBotAnimation

GRAPH_POINTS = 60


class PingGraph(ctk.CTkFrame):
    """Лінійний графік історії пінгу (мс) з автомасштабуванням і розривами при втратах."""

    def __init__(self, master, color: str, height: int = 70, points: int = GRAPH_POINTS):
        super().__init__(master, fg_color="transparent")
        self.color = color
        self.points = points
        self.history = deque([None] * points, maxlen=points)

        self.canvas = tk.Canvas(self, height=height, bg="#1a1a1a", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._redraw())

    def push(self, value: float | None) -> None:
        self.history.append(value)
        self._redraw()

    def clear(self) -> None:
        self.history = deque([None] * self.points, maxlen=self.points)
        self._redraw()

    def _redraw(self) -> None:
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
            text="Середній: —  ·  Джитер: —  ·  Втрати: —",
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
            self.value_label.configure(text="таймаут", text_color="#ff5c7a")
        else:
            self.value_label.configure(text=f"{latency:.0f} мс", text_color=("gray10", "gray90"))
        self.graph.push(latency)

        avg = f"{stats['avg']:.0f} мс" if stats["avg"] is not None else "—"
        jitter = f"{stats['jitter']:.0f} мс" if stats["jitter"] is not None else "—"
        loss = stats["loss_percent"]
        loss_text = f"{loss:.0f}%"
        loss_color = "#ff5c7a" if loss > 0 else "gray"

        self.stats_label.configure(
            text=f"Середній: {avg}  ·  Джитер: {jitter}  ·  Втрати: {loss_text}",
            text_color=loss_color,
        )

    def reset(self, idle_text: str = "Середній: —  ·  Джитер: —  ·  Втрати: —") -> None:
        self.value_label.configure(text="—", text_color=("gray10", "gray90"))
        self.stats_label.configure(text=idle_text, text_color="gray")
        self.graph.clear()


class TestHistoryTable(ctk.CTkFrame):
    """Маленька таблиця останніх тестів: дата, пінг, джитер, втрати, оцінка."""

    COLUMNS = ("Дата", "Пінг", "Джитер", "Втрати", "Оцінка")

    def __init__(self, master):
        super().__init__(master, corner_radius=10)
        self._rows: list[list[ctk.CTkLabel]] = []

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 4))
        for i, title in enumerate(self.COLUMNS):
            header.grid_columnconfigure(i, weight=1)
            ctk.CTkLabel(
                header, text=title, font=ctk.CTkFont(size=11, weight="bold"), text_color="gray",
            ).grid(row=0, column=i, sticky="w")

        self.rows_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.rows_frame.pack(fill="x", padx=12, pady=(0, 10))
        for i in range(len(self.COLUMNS)):
            self.rows_frame.grid_columnconfigure(i, weight=1)

        self.empty_label = ctk.CTkLabel(
            self.rows_frame, text="Історія тестів порожня", text_color="gray",
            font=ctk.CTkFont(size=11),
        )
        self.empty_label.grid(row=0, column=0, columnspan=len(self.COLUMNS), sticky="w", pady=4)

    def set_data(self, entries: list) -> None:
        for row in self._rows:
            for label in row:
                label.destroy()
        self._rows.clear()

        if not entries:
            self.empty_label.grid()
            return
        self.empty_label.grid_remove()

        for r, entry in enumerate(entries):
            avg = entry.get("avg")
            jitter = entry.get("jitter")
            label_text = entry.get("label", "—")
            values = (
                entry.get("date", "—"),
                f"{avg:.0f} мс" if avg is not None else "—",
                f"{jitter:.0f} мс" if jitter is not None else "—",
                f"{entry.get('loss', 0):.0f}%",
                label_text,
            )
            row_labels = []
            for c, value in enumerate(values):
                color = network_core.RATING_COLOR_BY_LABEL.get(value) if c == 4 else None
                lbl = ctk.CTkLabel(
                    self.rows_frame, text=str(value), font=ctk.CTkFont(size=11),
                    text_color=color or ("gray10", "gray90"), anchor="w",
                )
                lbl.grid(row=r, column=c, sticky="w", pady=2)
                row_labels.append(lbl)
            self._rows.append(row_labels)


class NetworkTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self._live_workers = []

        self._test_workers = []
        self._test_after_id = None
        self._test_started_at = 0.0
        self._last_result_text = ""

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

        label = ctk.CTkLabel(row, text="Мережа", font=ctk.CTkFont(size=22, weight="bold"))
        label.grid(row=0, column=0, sticky="w")

        self.live_switch = ctk.CTkSwitch(row, text="Живий режим", command=self._on_toggle_live)
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

        self.test_custom_entry = ctk.CTkEntry(
            self.start_section, placeholder_text="Власна адреса (напр. google.com)", width=280,
        )
        self.test_custom_entry.pack(pady=(0, 14))

        self.start_button = ctk.CTkButton(
            self.start_section, text="Почати тест (30 с)", width=260, height=44,
            font=ctk.CTkFont(size=16, weight="bold"), command=self._start_test,
        )
        self.start_button.pack()

        self.bot = CleanerBotAnimation(self.test_frame, height=170)

        self.live_cards_frame = ctk.CTkFrame(self.test_frame, fg_color="transparent")
        self.live_cards_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.test_google_card = PingCard(self.live_cards_frame, "Google DNS (8.8.8.8)", color="#4fc3ff")
        self.test_google_card.grid(row=0, column=0, padx=6, pady=6, sticky="nsew")

        self.test_cloudflare_card = PingCard(self.live_cards_frame, "Cloudflare (1.1.1.1)", color="#2ee59d")
        self.test_cloudflare_card.grid(row=0, column=1, padx=6, pady=6, sticky="nsew")

        self.test_custom_card = PingCard(self.live_cards_frame, "Власна адреса", color="#c77dff")
        self.test_custom_card.grid(row=0, column=2, padx=6, pady=6, sticky="nsew")

        self._build_result_section()

        history_wrap = ctk.CTkFrame(self.test_frame, fg_color="transparent")
        history_wrap.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(
            history_wrap, text="Історія тестів", font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(0, 6))
        self.history_table = TestHistoryTable(history_wrap)
        self.history_table.pack(fill="x")
        self.history_table.set_data(network_core.load_test_history())

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
            buttons, text="Повторити тест", width=160, command=self._start_test,
        )
        self.repeat_button.pack(side="left", padx=(0, 8))

        self.copy_button = ctk.CTkButton(
            buttons, text="Скопіювати результат", width=180, command=self._copy_result,
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
            self.test_custom_card.set_title(f"Власна адреса ({custom_host})")
            self.test_custom_card.reset()
            targets.append((self.test_custom_card, custom_host))
        else:
            self.test_custom_card.set_title("Власна адреса")
            self.test_custom_card.reset(idle_text="Адресу не задано")

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
        self.bot.start("Вимірюємо мережу… 30 с", tool="scan")
        self._tick_test()

    def _tick_test(self):
        if not self.winfo_exists():
            return

        elapsed = time.monotonic() - self._test_started_at
        remaining = max(0.0, network_core.TEST_DURATION_SEC - elapsed)
        progress = min(1.0, elapsed / network_core.TEST_DURATION_SEC)
        self.bot.update(f"Вимірюємо мережу… {remaining:.0f} с", progress)

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
            "label": rating["label"],
        }
        history = network_core.save_test_result(entry)
        self.history_table.set_data(history)

        success = rating["level"] <= 1
        self.bot.finish("Готово!" if success else "Хм, не дуже...", success=success)

        self._last_result_text = self._build_result_text(stats, rating, date_text)
        self._render_result(stats, rating)

    def _make_test_callback(self, card: PingCard):
        def callback(host, latency, stats):
            self.after(0, self._apply_test_update, card, latency, stats)
        return callback

    def _apply_test_update(self, card: PingCard, latency, stats):
        if not self.winfo_exists():
            return
        card.apply(latency, stats)

    def _render_result(self, stats: dict, rating: dict) -> None:
        self.result_badge.configure(text=rating["label"], fg_color=rating["color"], text_color="white")

        def fmt(v):
            return f"{v:.0f} мс" if v is not None else "—"

        stats_text = (
            f"Пінг: {fmt(stats['avg'])}  (мін {fmt(stats['min'])} · макс {fmt(stats['max'])})\n"
            f"Джитер: {fmt(stats['jitter'])}   ·   Втрати пакетів: {stats['loss_percent']:.0f}%"
        )
        self.result_stats_label.configure(text=stats_text)
        self.result_notes_label.configure(text="\n".join(f"•  {n}" for n in rating["notes"]))

        self.result_frame.pack(fill="x", pady=(0, 16))

    def _build_result_text(self, stats: dict, rating: dict, date_text: str) -> str:
        def fmt(v):
            return f"{v:.0f} мс" if v is not None else "—"

        lines = [
            f"Тест мережі — {date_text}",
            f"Оцінка: {rating['label']}",
            f"Пінг: {fmt(stats['avg'])} (мін {fmt(stats['min'])}, макс {fmt(stats['max'])})",
            f"Джитер: {fmt(stats['jitter'])}",
            f"Втрати пакетів: {stats['loss_percent']:.0f}%",
        ]
        if rating["notes"]:
            lines.append("Висновки:")
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
            controls, placeholder_text="Власна адреса (напр. google.com)", width=260,
        )
        self.live_custom_entry.pack(side="left", padx=(0, 10))

        self.live_start_button = ctk.CTkButton(
            controls, text="Старт", width=90, command=self._start_live,
        )
        self.live_start_button.pack(side="left", padx=(0, 6))

        self.live_stop_button = ctk.CTkButton(
            controls, text="Стоп", width=90, state="disabled",
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

        self.live_custom_card = PingCard(cards_frame, "Власна адреса", color="#c77dff")
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
            self.live_custom_card.set_title(f"Власна адреса ({custom_host})")
            self.live_custom_card.reset()
            targets.append((self.live_custom_card, custom_host))
        else:
            self.live_custom_card.set_title("Власна адреса")
            self.live_custom_card.reset(idle_text="Адресу не задано")

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
            self.after(0, self._apply_live_update, card, latency, stats)
        return callback

    def _apply_live_update(self, card: PingCard, latency, stats):
        if not self.winfo_exists():
            return
        card.apply(latency, stats)

    # -------------------------------------------------------------- misc

    def _on_destroy(self, event):
        if event.widget is not self:
            return

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
