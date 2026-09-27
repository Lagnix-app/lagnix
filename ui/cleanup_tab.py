"""Вкладка «Очищення» — сканування й видалення тимчасових файлів Windows."""

import threading
from tkinter import messagebox

import customtkinter as ctk

from core import cleanup as cleanup_core


class CleanupCard(ctk.CTkFrame):
    """Картка однієї цілі очищення: розмір, кількість файлів, кнопки «Сканувати»/«Очистити»."""

    def __init__(self, master, key: str, label: str, on_scan, on_clean):
        super().__init__(master, corner_radius=10)
        self.key = key
        self.scanned = None
        self._on_scan = on_scan
        self._on_clean = on_clean

        ctk.CTkLabel(self, text=label, font=ctk.CTkFont(size=15, weight="bold")).pack(
            padx=14, pady=(12, 4), anchor="w"
        )

        self.status_label = ctk.CTkLabel(self, text="Розмір не визначено", text_color="gray", anchor="w")
        self.status_label.pack(fill="x", padx=14, anchor="w")

        self.result_label = ctk.CTkLabel(self, text="", text_color="#2fa572", anchor="w")
        self.result_label.pack(fill="x", padx=14, pady=(2, 8), anchor="w")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=14, pady=(0, 12))

        self.scan_button = ctk.CTkButton(
            buttons, text="Сканувати", width=110, command=lambda: self._on_scan(self)
        )
        self.scan_button.pack(side="left", padx=(0, 8))

        self.clean_button = ctk.CTkButton(
            buttons,
            text="Очистити",
            width=110,
            state="disabled",
            fg_color="#8b2c2c",
            hover_color="#a83a3a",
            command=lambda: self._on_clean(self),
        )
        self.clean_button.pack(side="left")

    def set_busy(self, message: str) -> None:
        self.scan_button.configure(state="disabled")
        self.clean_button.configure(state="disabled")
        self.status_label.configure(text=message, text_color="gray")

    def show_scan_result(self, result: dict) -> None:
        self.scanned = result
        self.scan_button.configure(state="normal")
        self.result_label.configure(text="")

        if not result["exists"]:
            self.status_label.configure(text="Папка недоступна", text_color="gray")
            self.clean_button.configure(state="disabled")
        elif result["file_count"] == 0:
            self.status_label.configure(text="Немає файлів для очищення", text_color="gray")
            self.clean_button.configure(state="disabled")
        else:
            size_text = cleanup_core.format_size(result["size_bytes"])
            self.status_label.configure(
                text=f"Знайдено {result['file_count']} файлів · {size_text}",
                text_color=("gray10", "gray90"),
            )
            self.clean_button.configure(state="normal")

    def show_clean_result(self, result: dict) -> None:
        self.scan_button.configure(state="normal")
        self.clean_button.configure(state="disabled")

        freed_text = cleanup_core.format_size(result["freed_bytes"])
        message = f"Звільнено {freed_text} · видалено {result['deleted_count']} файлів"
        if result["skipped_count"]:
            message += f", пропущено {result['skipped_count']} (зайняті)"
        self.result_label.configure(text=message, text_color="#2fa572")
        self.status_label.configure(text="Немає файлів для очищення", text_color="gray")


class CleanupTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="Очищення", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, padx=20, pady=(20, 5), sticky="w"
        )
        ctk.CTkLabel(
            self,
            text="Сканування й видалення тимчасових файлів. Зайняті файли пропускаються без помилок.",
            text_color="gray",
        ).grid(row=1, column=0, padx=20, pady=(0, 15), sticky="w")

        cards_frame = ctk.CTkFrame(self, fg_color="transparent")
        cards_frame.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="nsew")

        self.cards = {}
        targets = cleanup_core.get_targets()
        cards_frame.grid_columnconfigure(tuple(range(len(targets))), weight=1)

        for i, target in enumerate(targets):
            card = CleanupCard(cards_frame, target["key"], target["label"], self._scan, self._clean)
            card.grid(row=0, column=i, padx=6, pady=6, sticky="nsew")
            self.cards[target["key"]] = card
            self._scan(card)

    # -------------------------------------------------------------- scan

    def _scan(self, card: CleanupCard):
        card.set_busy("Сканування...")

        def worker():
            result = cleanup_core.scan_target(card.key)
            self.after(0, self._on_scan_result, card, result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_scan_result(self, card: CleanupCard, result: dict):
        if not self.winfo_exists():
            return
        card.show_scan_result(result)

    # ------------------------------------------------------------- clean

    def _clean(self, card: CleanupCard):
        if not card.scanned or card.scanned["file_count"] == 0:
            return

        size_text = cleanup_core.format_size(card.scanned["size_bytes"])
        confirmed = messagebox.askyesno(
            "Підтвердження",
            f"Видалити {card.scanned['file_count']} файлів ({size_text})\n"
            f"із «{card.scanned['path']}»?",
            parent=self,
        )
        if not confirmed:
            return

        card.set_busy("Очищення...")

        def worker():
            result = cleanup_core.clean_target(card.key)
            self.after(0, self._on_clean_result, card, result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_clean_result(self, card: CleanupCard, result: dict):
        if not self.winfo_exists():
            return
        card.show_clean_result(result)
