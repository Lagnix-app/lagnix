"""Вкладка «Монітор» — CPU, RAM, GPU у реальному часі та список процесів."""

import threading
import tkinter as tk
from collections import deque
from tkinter import messagebox

import customtkinter as ctk

from core import monitor as monitor_core
from core.logging_setup import get_logger
from core.system_processes import is_protected

UPDATE_INTERVAL_SEC = 1.0
GRAPH_POINTS = 60

_logger = get_logger(__name__)


class MiniGraph(ctk.CTkFrame):
    """Легкий лінійний графік історії значень 0..max_value на tkinter.Canvas."""

    def __init__(self, master, color: str, max_value: float = 100.0, height: int = 60):
        super().__init__(master, fg_color="transparent")
        self.color = color
        self.max_value = max_value
        self.history = deque([0.0] * GRAPH_POINTS, maxlen=GRAPH_POINTS)

        self.canvas = tk.Canvas(self, height=height, bg="#1a1a1a", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._redraw())

    def push(self, value: float) -> None:
        self.history.append(max(0.0, min(value, self.max_value)))
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.delete("all")
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width <= 1 or height <= 1:
            return

        n = len(self.history)
        step = width / (n - 1)
        points = []
        for i, value in enumerate(self.history):
            x = i * step
            y = height - (value / self.max_value) * height
            points.extend((x, y))

        self.canvas.create_line(*points, fill=self.color, width=2, smooth=True)


class ProcessRow(ctk.CTkFrame):
    """Один рядок у списку процесів: назва, значення, кнопка завершення."""

    def __init__(self, master, on_terminate):
        super().__init__(master, fg_color="transparent")
        self._on_terminate = on_terminate
        self.pid = None
        self.name = ""

        self.grid_columnconfigure(0, weight=1)

        self.name_label = ctk.CTkLabel(self, text="—", anchor="w")
        self.name_label.grid(row=0, column=0, sticky="ew", padx=(4, 8))

        self.value_label = ctk.CTkLabel(self, text="", width=60, anchor="e")
        self.value_label.grid(row=0, column=1, sticky="e", padx=(0, 8))

        self.kill_button = ctk.CTkButton(
            self, text="Завершити", width=90,
            fg_color="#a8283f", hover_color="#ff5c7a",
            command=self._handle_click, state="disabled",
        )
        self.kill_button.grid(row=0, column=2, sticky="e")

        self.status_label = ctk.CTkLabel(self, text="системний", width=90, text_color="gray")

    def update_data(self, pid: int, name: str, value_text: str, protected: bool = False) -> None:
        self.pid = pid
        self.name = name
        self.name_label.configure(text=f"{name} (PID {pid})")
        self.value_label.configure(text=value_text)

        if protected:
            self.kill_button.grid_remove()
            self.status_label.grid(row=0, column=2, sticky="e")
        else:
            self.status_label.grid_remove()
            self.kill_button.configure(state="normal")
            self.kill_button.grid(row=0, column=2, sticky="e")

    def clear(self) -> None:
        self.pid = None
        self.name = ""
        self.name_label.configure(text="—")
        self.value_label.configure(text="")
        self.status_label.grid_remove()
        self.kill_button.grid_remove()

    def _handle_click(self) -> None:
        if self.pid is not None:
            self._on_terminate(self.pid, self.name)


class MonitorTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self._stop_event = threading.Event()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._build_header()
        self._build_usage_cards()
        self._build_warning_banner()
        self._build_process_lists()

        self.bind("<Destroy>", self._on_destroy)

        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    # ------------------------------------------------------------------ UI

    def _build_header(self):
        label = ctk.CTkLabel(self, text="Монітор", font=ctk.CTkFont(size=22, weight="bold"))
        label.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

    def _build_usage_cards(self):
        cards_frame = ctk.CTkFrame(self, fg_color="transparent")
        cards_frame.grid(row=1, column=0, padx=20, pady=(0, 6), sticky="ew")
        cards_frame.grid_columnconfigure((0, 1, 2), weight=1)

        (self.cpu_card, self.cpu_value_label,
         self.cpu_graph, self.cpu_extra_label) = self._make_usage_card(
            cards_frame, "CPU", 0, color="#4fc3ff"
        )
        (self.ram_card, self.ram_value_label,
         self.ram_graph, self.ram_extra_label) = self._make_usage_card(
            cards_frame, "RAM", 1, color="#2ee59d"
        )
        (self.gpu_card, self.gpu_value_label,
         self.gpu_graph, self.gpu_extra_label) = self._make_usage_card(
            cards_frame, "GPU", 2, color="#c77dff"
        )

    def _make_usage_card(self, master, title, column, color):
        card = ctk.CTkFrame(master, corner_radius=10)
        card.grid(row=0, column=column, padx=6, pady=6, sticky="nsew")

        title_row = ctk.CTkFrame(card, fg_color="transparent")
        title_row.pack(fill="x", padx=12, pady=(10, 4))

        ctk.CTkLabel(title_row, text=title, font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        value_label = ctk.CTkLabel(title_row, text="—", font=ctk.CTkFont(size=14, weight="bold"))
        value_label.pack(side="right")

        graph = MiniGraph(card, color=color)
        graph.pack(fill="x", padx=12, pady=(0, 6))

        extra_label = ctk.CTkLabel(card, text="", text_color="gray", anchor="w", justify="left")
        extra_label.pack(fill="x", padx=12, pady=(0, 10), anchor="w")

        return card, value_label, graph, extra_label

    def _build_warning_banner(self):
        self.warning_label = ctk.CTkLabel(
            self, text="", text_color="#ff5c7a",
            font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
        )
        self.warning_label.grid(row=2, column=0, padx=20, pady=(0, 6), sticky="ew")

    def _build_process_lists(self):
        lists_frame = ctk.CTkFrame(self, fg_color="transparent")
        lists_frame.grid(row=3, column=0, padx=20, pady=(0, 20), sticky="nsew")
        lists_frame.grid_columnconfigure((0, 1), weight=1)
        lists_frame.grid_rowconfigure(0, weight=1)

        self.cpu_rows = self._make_process_column(lists_frame, "Топ-10 за CPU", 0)
        self.ram_rows = self._make_process_column(lists_frame, "Топ-10 за RAM", 1)

    def _make_process_column(self, master, title, column):
        container = ctk.CTkFrame(master, corner_radius=10)
        container.grid(row=0, column=column, padx=6, sticky="nsew")

        ctk.CTkLabel(
            container, text=title, font=ctk.CTkFont(size=14, weight="bold")
        ).pack(padx=12, pady=(10, 4), anchor="w")

        scroll = ctk.CTkScrollableFrame(container, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=6, pady=(0, 10))

        rows = [ProcessRow(scroll, on_terminate=self._confirm_terminate) for _ in range(10)]
        for row in rows:
            row.pack(fill="x", pady=2)
        return rows

    # -------------------------------------------------------------- worker

    def _worker_loop(self):
        try:
            monitor_core.prime()
        except Exception:
            _logger.exception("Не вдалося ініціалізувати збір даних монітора (prime)")

        while not self._stop_event.is_set():
            try:
                data = monitor_core.collect_snapshot()
            except Exception as exc:
                _logger.exception("Помилка збору даних монітора")
                data = {"error": str(exc)}

            if self._stop_event.is_set():
                break

            self.after(0, self._apply_snapshot, data)
            self._stop_event.wait(UPDATE_INTERVAL_SEC)

    def _on_destroy(self, event):
        if event.widget is self:
            self._stop_event.set()

    # --------------------------------------------------------------- apply

    def _apply_snapshot(self, data: dict):
        if not self.winfo_exists():
            return

        if "error" in data:
            self.warning_label.configure(text=f" ⚠ Помилка збору даних монітора: {data['error']}")
            return

        threshold = data["temp_threshold"]
        warnings = []

        self.cpu_value_label.configure(text=f"{data['cpu_percent']:.0f}%")
        self.cpu_graph.push(data["cpu_percent"])

        cpu_temp = data["cpu_temp"]
        if cpu_temp is None:
            self.cpu_extra_label.configure(text="Темп.: недоступно")
        else:
            self.cpu_extra_label.configure(text=f"Темп.: {cpu_temp:.0f}°C")
            if cpu_temp > threshold:
                warnings.append(f"CPU перегрівається: {cpu_temp:.0f}°C (поріг {threshold}°C)")

        self.ram_value_label.configure(text=f"{data['ram_percent']:.0f}%")
        self.ram_graph.push(data["ram_percent"])
        self.ram_extra_label.configure(
            text=f"{data['ram_used_gb']:.1f} / {data['ram_total_gb']:.1f} ГБ"
        )

        gpu = data["gpu"]
        if gpu is None:
            self.gpu_value_label.configure(text="—")
            self.gpu_graph.push(0.0)
            self.gpu_extra_label.configure(text="недоступно (nvidia-smi не знайдено)")
        else:
            self.gpu_value_label.configure(text=f"{gpu['load_percent']:.0f}%")
            self.gpu_graph.push(gpu["load_percent"])
            self.gpu_extra_label.configure(
                text=(
                    f"Пам'ять: {gpu['mem_used_mb']:.0f} / {gpu['mem_total_mb']:.0f} МБ\n"
                    f"Темп.: {gpu['temperature_c']:.0f}°C"
                )
            )
            if gpu["temperature_c"] > threshold:
                warnings.append(
                    f"GPU перегрівається: {gpu['temperature_c']:.0f}°C (поріг {threshold}°C)"
                )

        self.warning_label.configure(text=(" ⚠ " + "  |  ".join(warnings)) if warnings else "")

        self._fill_process_rows(self.cpu_rows, data["top_cpu"], "cpu_percent")
        self._fill_process_rows(self.ram_rows, data["top_ram"], "memory_percent")

    def _fill_process_rows(self, rows, processes, percent_key):
        for i, row in enumerate(rows):
            if i < len(processes):
                proc = processes[i]
                row.update_data(
                    pid=proc["pid"],
                    name=proc["name"],
                    value_text=f"{proc[percent_key]:.1f}%",
                    protected=is_protected(proc["name"]),
                )
            else:
                row.clear()

    # ---------------------------------------------------------- terminate

    def _confirm_terminate(self, pid: int, name: str):
        confirmed = messagebox.askyesno(
            "Підтвердження",
            f"Завершити процес «{name}» (PID {pid})?",
            parent=self,
        )
        if not confirmed:
            return

        def worker():
            success, error = monitor_core.terminate_process(pid)
            self.after(0, self._on_terminate_result, name, pid, success, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_terminate_result(self, name, pid, success, error):
        if not self.winfo_exists():
            return
        if not success:
            messagebox.showerror(
                "Помилка",
                f"Не вдалося завершити «{name}» (PID {pid}): {error}",
                parent=self,
            )
