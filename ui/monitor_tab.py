"""Вкладка «Монітор» — заглушка."""

import customtkinter as ctk


class MonitorTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        label = ctk.CTkLabel(
            self,
            text="Монітор",
            font=ctk.CTkFont(size=22, weight="bold")
        )
        label.pack(pady=20, padx=20, anchor="w")

        placeholder = ctk.CTkLabel(
            self,
            text="Розділ у розробці...",
            text_color="gray"
        )
        placeholder.pack(padx=20, anchor="w")
