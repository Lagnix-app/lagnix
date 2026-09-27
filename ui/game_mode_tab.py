"""Вкладка «Ігровий режим» — профілі закриття процесів, план живлення, автозапуск для ігор."""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from core import game_mode as game_mode_core

GAME_CHECK_INTERVAL_SEC = 2.0
PROFILE_NAMES = ("Гра", "Стрім", "Робота")


class GameModeTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self.state = game_mode_core.load_game_mode()
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._auto_enabled = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_profile_panel()
        self._build_process_panel()
        self._build_games_panel()

        self._refresh_process_list()
        self._set_active_ui(self.state.get("is_active", False))

        self.bind("<Destroy>", self._on_destroy)

        self._watcher = threading.Thread(target=self._watch_games_loop, daemon=True)
        self._watcher.start()

    # ------------------------------------------------------------------ UI

    def _build_header(self):
        label = ctk.CTkLabel(self, text="Ігровий режим", font=ctk.CTkFont(size=22, weight="bold"))
        label.grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 10), sticky="w")

    def _build_profile_panel(self):
        panel = ctk.CTkFrame(self, corner_radius=10)
        panel.grid(row=1, column=0, padx=(20, 10), pady=(0, 20), sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(panel, text="Профіль", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, padx=12, pady=(12, 6), sticky="w"
        )

        self.profile_var = tk.StringVar(value=self.state["active_profile"])
        self.profile_menu = ctk.CTkOptionMenu(
            panel, variable=self.profile_var, values=list(PROFILE_NAMES),
            command=self._on_profile_selected,
        )
        self.profile_menu.grid(row=1, column=0, padx=12, pady=(0, 10), sticky="ew")

        ctk.CTkLabel(panel, text="План живлення профілю", text_color="gray").grid(
            row=2, column=0, padx=12, pady=(0, 4), sticky="w"
        )
        self.power_plan_var = tk.StringVar()
        self.power_plan_menu = ctk.CTkOptionMenu(
            panel, variable=self.power_plan_var,
            values=list(game_mode_core.POWER_PLANS.keys()),
            command=self._on_power_plan_selected,
        )
        self.power_plan_menu.grid(row=3, column=0, padx=12, pady=(0, 12), sticky="ew")

        self.status_label = ctk.CTkLabel(panel, text="", font=ctk.CTkFont(size=13, weight="bold"))
        self.status_label.grid(row=4, column=0, padx=12, pady=(0, 10), sticky="w")

        buttons_row = ctk.CTkFrame(panel, fg_color="transparent")
        buttons_row.grid(row=5, column=0, padx=12, pady=(0, 12), sticky="ew")
        buttons_row.grid_columnconfigure((0, 1), weight=1)

        self.enable_button = ctk.CTkButton(
            buttons_row, text="Увімкнути", fg_color="#2fa572", hover_color="#268a5f",
            command=self._on_enable_clicked,
        )
        self.enable_button.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        self.disable_button = ctk.CTkButton(
            buttons_row, text="Вимкнути", fg_color="#8b2c2c", hover_color="#a83a3a",
            command=self._on_disable_clicked,
        )
        self.disable_button.grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self._load_profile_into_ui(self.state["active_profile"])

    def _build_process_panel(self):
        panel = ctk.CTkFrame(self, corner_radius=10)
        panel.grid(row=1, column=1, padx=(10, 20), pady=(0, 20), sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)

        header_row = ctk.CTkFrame(panel, fg_color="transparent")
        header_row.grid(row=0, column=0, padx=12, pady=(12, 6), sticky="ew")
        header_row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header_row, text="Процеси для закриття", font=ctk.CTkFont(size=14, weight="bold")
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            header_row, text="Оновити", width=90, command=self._refresh_process_list,
        ).grid(row=0, column=1, sticky="e")

        self.process_scroll = ctk.CTkScrollableFrame(panel, fg_color="transparent")
        self.process_scroll.grid(row=1, column=0, padx=6, pady=(0, 12), sticky="nsew")

    def _build_games_panel(self):
        panel = ctk.CTkFrame(self, corner_radius=10)
        panel.grid(row=2, column=0, columnspan=2, padx=20, pady=(0, 20), sticky="ew")
        panel.grid_columnconfigure(0, weight=1)

        header_row = ctk.CTkFrame(panel, fg_color="transparent")
        header_row.grid(row=0, column=0, padx=12, pady=(12, 6), sticky="ew")
        header_row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header_row, text="Ігри для автозапуску режиму", font=ctk.CTkFont(size=14, weight="bold")
        ).grid(row=0, column=0, sticky="w")

        self.game_detect_label = ctk.CTkLabel(header_row, text="Ігри не запущені", text_color="gray")
        self.game_detect_label.grid(row=0, column=1, sticky="e")

        entry_row = ctk.CTkFrame(panel, fg_color="transparent")
        entry_row.grid(row=1, column=0, padx=12, pady=(0, 8), sticky="ew")
        entry_row.grid_columnconfigure(0, weight=1)

        self.game_entry = ctk.CTkEntry(entry_row, placeholder_text="назва.exe")
        self.game_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkButton(entry_row, text="Огляд...", width=90, command=self._browse_game).grid(
            row=0, column=1, padx=(0, 6)
        )
        ctk.CTkButton(entry_row, text="Додати", width=90, command=self._add_game).grid(
            row=0, column=2
        )

        list_row = ctk.CTkFrame(panel, fg_color="transparent")
        list_row.grid(row=2, column=0, padx=12, pady=(0, 12), sticky="ew")
        list_row.grid_columnconfigure(0, weight=1)

        self.games_listbox = tk.Listbox(
            list_row, height=5, bg="#1a1a1a", fg="white",
            selectbackground="#3b8ed0", highlightthickness=0, borderwidth=0,
        )
        self.games_listbox.grid(row=0, column=0, sticky="ew")

        ctk.CTkButton(
            list_row, text="Видалити", width=110, fg_color="#8b2c2c", hover_color="#a83a3a",
            command=self._remove_selected_game,
        ).grid(row=0, column=1, padx=(8, 0), sticky="n")

        self._refresh_games_listbox()

    # --------------------------------------------------------- профіль/UI

    def _load_profile_into_ui(self, profile_name: str) -> None:
        profile = self.state["profiles"].get(profile_name, {})
        self.power_plan_var.set(game_mode_core.power_plan_name(profile.get("power_plan", "")))

    def _on_profile_selected(self, value: str) -> None:
        self.state["active_profile"] = value
        game_mode_core.save_game_mode(self.state)
        self._load_profile_into_ui(value)
        self._refresh_process_list()

    def _on_power_plan_selected(self, value: str) -> None:
        profile_name = self.profile_var.get()
        guid = game_mode_core.POWER_PLANS.get(value, "")
        self.state["profiles"][profile_name]["power_plan"] = guid
        game_mode_core.save_game_mode(self.state)

    def _set_active_ui(self, is_active: bool) -> None:
        if not self.winfo_exists():
            return
        if is_active:
            self.status_label.configure(text="● Режим увімкнено", text_color="#2fa572")
            self.enable_button.configure(state="disabled")
            self.disable_button.configure(state="normal")
        else:
            self.status_label.configure(text="○ Режим вимкнено", text_color="gray")
            self.enable_button.configure(state="normal")
            self.disable_button.configure(state="disabled")

    # ------------------------------------------------------------- процеси

    def _refresh_process_list(self) -> None:
        for child in self.process_scroll.winfo_children():
            child.destroy()

        profile_name = self.profile_var.get()
        selected = set(self.state["profiles"].get(profile_name, {}).get("processes", []))

        for proc in game_mode_core.get_running_process_names():
            name = proc["name"]
            label = name if proc["count"] <= 1 else f"{name} ({proc['count']})"
            if proc["protected"]:
                label += " (системний)"

            var = tk.BooleanVar(value=(name in selected and not proc["protected"]))
            checkbox = ctk.CTkCheckBox(
                self.process_scroll, text=label, variable=var,
                command=lambda n=name, v=var: self._on_process_toggle(n, v),
            )
            checkbox.pack(fill="x", padx=6, pady=2, anchor="w")

            if proc["protected"]:
                checkbox.configure(state="disabled")

    def _on_process_toggle(self, name: str, var: tk.BooleanVar) -> None:
        profile_name = self.profile_var.get()
        processes = self.state["profiles"][profile_name].setdefault("processes", [])
        if var.get():
            if name not in processes:
                processes.append(name)
        else:
            if name in processes:
                processes.remove(name)
        game_mode_core.save_game_mode(self.state)

    # ---------------------------------------------------------------- ігри

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

        self.state["games"].append(name)
        game_mode_core.save_game_mode(self.state)
        self.game_entry.delete(0, "end")
        self._refresh_games_listbox()

    def _remove_selected_game(self) -> None:
        selection = self.games_listbox.curselection()
        if not selection:
            return

        index = selection[0]
        if 0 <= index < len(self.state["games"]):
            del self.state["games"][index]
            game_mode_core.save_game_mode(self.state)
            self._refresh_games_listbox()

    def _refresh_games_listbox(self) -> None:
        self.games_listbox.delete(0, "end")
        for name in self.state["games"]:
            self.games_listbox.insert("end", name)

    # ------------------------------------------------------- увімкнути/ні

    def _on_enable_clicked(self) -> None:
        profile_name = self.profile_var.get()
        profile = self.state["profiles"].get(profile_name, {})
        processes = profile.get("processes", [])
        running_names = game_mode_core.get_running_process_name_set()
        to_close = [p for p in processes if p.lower() in running_names]

        if to_close:
            message = "Закрити такі процеси перед увімкненням профілю «{}»?\n\n{}".format(
                profile_name, "\n".join(to_close)
            )
            if not messagebox.askyesno("Підтвердження", message, parent=self):
                return

        self.enable_button.configure(state="disabled")

        def worker():
            with self._lock:
                state, results = game_mode_core.activate_profile(self.state, profile_name)
                self.state = state
                self._auto_enabled = False
            self.after(0, self._on_activate_done, results)

        threading.Thread(target=worker, daemon=True).start()

    def _on_activate_done(self, results) -> None:
        self._set_active_ui(True)
        failed = [f"{name}: {error}" for name, success, error in results if not success]
        if failed and self.winfo_exists():
            messagebox.showerror(
                "Помилка", "Не вдалося закрити деякі процеси:\n" + "\n".join(failed), parent=self
            )

    def _on_disable_clicked(self) -> None:
        self.disable_button.configure(state="disabled")

        def worker():
            with self._lock:
                state = game_mode_core.deactivate_profile(self.state)
                self.state = state
                self._auto_enabled = False
            self.after(0, self._set_active_ui, False)

        threading.Thread(target=worker, daemon=True).start()

    # -------------------------------------------------------------- ігри watcher

    def _watch_games_loop(self) -> None:
        was_running = False

        while not self._stop_event.is_set():
            games = list(self.state.get("games", []))
            detected = []
            if games:
                running_names = game_mode_core.get_running_process_name_set()
                game_set = {g.lower() for g in games}
                detected = sorted(game_set & running_names)

            is_running_now = bool(detected)

            if is_running_now and not was_running:
                with self._lock:
                    if not self.state.get("is_active"):
                        profile_name = self.state.get("active_profile", "Гра")
                        state, _results = game_mode_core.activate_profile(self.state, profile_name)
                        self.state = state
                        self._auto_enabled = True
                        self.after(0, self._set_active_ui, True)
            elif not is_running_now and was_running:
                with self._lock:
                    if self.state.get("is_active") and self._auto_enabled:
                        state = game_mode_core.deactivate_profile(self.state)
                        self.state = state
                        self._auto_enabled = False
                        self.after(0, self._set_active_ui, False)

            was_running = is_running_now
            detected_text = ("Виявлено: " + ", ".join(detected)) if detected else "Ігри не запущені"
            self.after(0, self._safe_update_detect_label, detected_text)

            self._stop_event.wait(GAME_CHECK_INTERVAL_SEC)

    def _safe_update_detect_label(self, text: str) -> None:
        if self.winfo_exists():
            self.game_detect_label.configure(text=text)

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            self._stop_event.set()
