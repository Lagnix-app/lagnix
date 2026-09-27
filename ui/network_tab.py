"""Вкладка «Мережа» — пінг у реальному часі до заданих хостів."""

import tkinter as tk
from collections import deque

import customtkinter as ctk

from core import network as network_core

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
            self.value_label.configure(text="таймаут", text_color="#e05252")
        else:
            self.value_label.configure(text=f"{latency:.0f} мс", text_color=("gray10", "gray90"))
        self.graph.push(latency)

        avg = f"{stats['avg']:.0f} мс" if stats["avg"] is not None else "—"
        jitter = f"{stats['jitter']:.0f} мс" if stats["jitter"] is not None else "—"
        loss = stats["loss_percent"]
        loss_text = f"{loss:.0f}%"
        loss_color = "#e05252" if loss > 0 else "gray"

        self.stats_label.configure(
            text=f"Середній: {avg}  ·  Джитер: {jitter}  ·  Втрати: {loss_text}",
            text_color=loss_color,
        )

    def reset(self, idle_text: str = "Середній: —  ·  Джитер: —  ·  Втрати: —") -> None:
        self.value_label.configure(text="—", text_color=("gray10", "gray90"))
        self.stats_label.configure(text=idle_text, text_color="gray")
        self.graph.clear()


class NetworkTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self._workers = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_controls()
        self._build_cards()

        self.bind("<Destroy>", self._on_destroy)

    # ------------------------------------------------------------------ UI

    def _build_header(self):
        label = ctk.CTkLabel(self, text="Мережа", font=ctk.CTkFont(size=22, weight="bold"))
        label.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

    def _build_controls(self):
        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="ew")

        self.custom_entry = ctk.CTkEntry(
            controls, placeholder_text="Власна адреса (напр. google.com)", width=260
        )
        self.custom_entry.pack(side="left", padx=(0, 10))

        self.start_button = ctk.CTkButton(controls, text="Старт", width=90, command=self._start)
        self.start_button.pack(side="left", padx=(0, 6))

        self.stop_button = ctk.CTkButton(
            controls,
            text="Стоп",
            width=90,
            state="disabled",
            fg_color="#8b2c2c",
            hover_color="#a83a3a",
            command=self._stop,
        )
        self.stop_button.pack(side="left")

    def _build_cards(self):
        cards_frame = ctk.CTkFrame(self, fg_color="transparent")
        cards_frame.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="nsew")
        cards_frame.grid_columnconfigure((0, 1, 2), weight=1)
        cards_frame.grid_rowconfigure(0, weight=1)

        self.google_card = PingCard(cards_frame, "Google DNS (8.8.8.8)", color="#3b8ed0")
        self.google_card.grid(row=0, column=0, padx=6, pady=6, sticky="nsew")

        self.cloudflare_card = PingCard(cards_frame, "Cloudflare (1.1.1.1)", color="#2fa572")
        self.cloudflare_card.grid(row=0, column=1, padx=6, pady=6, sticky="nsew")

        self.custom_card = PingCard(cards_frame, "Власна адреса", color="#c77dff")
        self.custom_card.grid(row=0, column=2, padx=6, pady=6, sticky="nsew")

    # -------------------------------------------------------------- control

    def _start(self):
        if self._workers:
            return

        self.google_card.reset()
        self.cloudflare_card.reset()

        targets = [
            (self.google_card, "8.8.8.8"),
            (self.cloudflare_card, "1.1.1.1"),
        ]

        custom_host = self.custom_entry.get().strip()
        if custom_host:
            self.custom_card.set_title(f"Власна адреса ({custom_host})")
            self.custom_card.reset()
            targets.append((self.custom_card, custom_host))
        else:
            self.custom_card.set_title("Власна адреса")
            self.custom_card.reset(idle_text="Адресу не задано")

        for card, host in targets:
            worker = network_core.PingWorker(host, on_update=self._make_callback(card))
            self._workers.append(worker)
            worker.start()

        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.custom_entry.configure(state="disabled")

    def _stop(self):
        for worker in self._workers:
            worker.stop()
        self._workers.clear()

        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.custom_entry.configure(state="normal")

    def _make_callback(self, card: PingCard):
        def callback(host, latency, stats):
            self.after(0, self._apply_update, card, latency, stats)
        return callback

    def _apply_update(self, card: PingCard, latency, stats):
        if not self.winfo_exists():
            return
        card.apply(latency, stats)

    def _on_destroy(self, event):
        if event.widget is self:
            for worker in self._workers:
                worker.stop()
            self._workers.clear()
