"""Вкладка «Система»: характеристики заліза, розумні підказки та звіт
«що гальмує мій ПК» (core/system_info.py)."""

import os
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox

import customtkinter as ctk

from core import game_mode as game_mode_core
from core import system_info as system_info_core
from core.logging_setup import get_logger
from ui.widgets.scroll import ScrollFrame
from ui import bg, theme
from ui.widgets.cleaner_bot import CleanerBotAnimation
from core.i18n import t

_logger = get_logger(__name__)


class InfoCard(ctk.CTkFrame):
    """Картка характеристики: заголовок і кілька рядків тексту."""

    def __init__(self, master, title: str):
        super().__init__(master, corner_radius=10)

        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=14, weight="bold")).pack(
            padx=14, pady=(12, 4), anchor="w"
        )
        self.body_label = ctk.CTkLabel(
            self, text=t("common.loading_dots"), text_color="gray", font=ctk.CTkFont(size=12),
            justify="left", anchor="w", wraplength=360,
        )
        self.body_label.pack(padx=14, pady=(0, 14), anchor="w")
        # перенос рядків — за шириною картки, а не фіксовані 360 dp
        tk.Misc.bind(self, "<Configure>", self._fit_wrap, "+")

    def _fit_wrap(self, event) -> None:
        wrap = max(round(event.width / self._get_widget_scaling()) - 32, 160)
        if self.body_label.cget("wraplength") != wrap:
            self.body_label.configure(wraplength=wrap)

    def set_lines(self, lines: list[str]) -> None:
        # set_text пропускає однаковий текст: частота CPU оновлюється кожні 1.5 с
        theme.set_text(self.body_label, "\n".join(lines), text_color=("gray10", "gray90"))

    def set_error(self, text: str = t("common.unavailable")) -> None:
        theme.set_text(self.body_label, text, text_color="gray")


class DiskCard(ctk.CTkFrame):
    """Картка диска: тип, смужка зайнятого місця (жовта > 80%, червона > 90%) і
    підпис із тими самими числами, що на смужці."""

    def __init__(self, master, disk: dict):
        super().__init__(master, corner_radius=10)

        top = theme.plain_frame(self)
        top.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(top, text=t("system.disk", letter=disk['letter']), font=ctk.CTkFont(size=14, weight="bold")).pack(
            side="left"
        )
        ctk.CTkLabel(top, text=disk["type"], text_color="gray", font=ctk.CTkFont(size=11)).pack(side="right")

        used = disk["used_percent"]
        color = system_info_core.disk_bar_color(used)
        bar = ctk.CTkProgressBar(self, height=10, progress_color=color)
        bar.set(max(0.0, min(1.0, used / 100)))
        bar.pack(fill="x", padx=14, pady=(4, 6))

        ctk.CTkLabel(
            self,
            text=system_info_core.format_disk_usage(disk),
            text_color=color if used > 80 else "gray",
            font=ctk.CTkFont(size=11),
        ).pack(padx=14, pady=(0, 12), anchor="w")


class TipCard(ctk.CTkFrame):
    """Жовта картка розумної підказки з кнопкою дії («Як виправити» або tip["button"])."""

    def __init__(self, master, tip: dict, on_fix):
        super().__init__(master, corner_radius=10, fg_color="#3a2f14", border_width=1, border_color="#e0a52f")

        row = theme.plain_frame(self)
        row.pack(fill="x", padx=14, pady=10)
        row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            row, text=tip["text"], text_color="#f0d080", font=ctk.CTkFont(size=12),
            wraplength=600, justify="left", anchor="w",
        ).grid(row=0, column=0, sticky="w")

        if tip.get("action"):
            ctk.CTkButton(
                row, text=tip.get("button", t("system.how_to_fix")), width=130, height=28, font=ctk.CTkFont(size=12),
                fg_color="#e0a52f", hover_color="#f0c060", text_color="#151c2c",
                command=lambda tw=tip: on_fix(tw),
            ).grid(row=0, column=1, padx=(10, 0))


class SystemTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")

        self._snapshot = None
        self._report_running = False
        self._report_stop_event = threading.Event()
        self._report_after_id = None
        self._report_started_at = 0.0
        self._last_report_text = ""
        self._visible = False
        self._freq_after_id = None
        self._cpu_current_ghz = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.scroll = ScrollFrame(self)
        self.scroll.grid(row=0, column=0, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_hardware_section()
        self._build_disks_section()
        self._build_monitors_section()
        self._build_tips_section()
        self._build_report_section()

        self.bind("<Destroy>", self._on_destroy)

        # Через after(0, ...), а не напряму: вкладки створюються ще до
        # MainWindow.mainloop(), і фоновий потік міг би викликати
        # self.after() ще до реального старту mainloop (Python 3.13+
        # кидає на це непіймане RuntimeError, і потік мовчки гине).
        self.after(0, self._load_snapshot)

    def on_visibility_changed(self, visible: bool) -> None:
        """Поточна частота CPU оновлюється, лише поки вкладку видно."""
        self._visible = visible
        if visible and self._freq_after_id is None:
            self._tick_cpu_freq()
        elif not visible and self._freq_after_id is not None:
            self.after_cancel(self._freq_after_id)
            self._freq_after_id = None

    def _tick_cpu_freq(self) -> None:
        self._freq_after_id = None
        if not self.winfo_exists() or not self._visible:
            return
        if theme.is_scrolling():
            self._freq_after_id = self.after(200, self._tick_cpu_freq)
            return
        self._update_cpu_freq()
        self._freq_after_id = self.after(1500, self._tick_cpu_freq)

    def _monitor_snapshot(self):
        tab = getattr(self.winfo_toplevel(), "tab_frames", {}).get("monitor")
        return tab.latest_snapshot() if tab is not None else None

    def _update_cpu_freq(self) -> None:
        """«3.60 ГГц базова · 4.42 ГГц зараз»: поточна — зі зрізу Монітора (той самий
        лічильник «% Processor Performance», що й там)."""
        if self._snapshot is None:
            return
        self._cpu_current_ghz = system_info_core.current_cpu_freq_ghz(self._monitor_snapshot())
        self._snapshot["cpu"]["current_ghz"] = self._cpu_current_ghz
        self.cpu_card.set_lines(self._cpu_lines(self._snapshot["cpu"]))

    # ------------------------------------------------------------------ UI

    def _build_header(self):
        row = theme.plain_frame(self.scroll)
        row.pack(fill="x", padx=6, pady=(14, 10))
        row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(row, text=t("tabs.system"), font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.copy_info_button = ctk.CTkButton(
            row, text=t("system.copy_info"), width=260,
            command=self._copy_system_info,
        )
        self.copy_info_button.grid(row=0, column=1, sticky="e")

    def _build_hardware_section(self):
        grid = theme.plain_frame(self.scroll)
        grid.pack(fill="x", padx=6, pady=(0, 10))
        grid.grid_columnconfigure((0, 1), weight=1)

        self.cpu_card = InfoCard(grid, t("system.cpu"))
        self.cpu_card.grid(row=0, column=0, padx=6, pady=6, sticky="nsew")

        self.gpu_card = InfoCard(grid, t("system.gpu"))
        self.gpu_card.grid(row=0, column=1, padx=6, pady=6, sticky="nsew")

        self.ram_card = InfoCard(grid, t("system.ram"))
        self.ram_card.grid(row=1, column=0, padx=6, pady=6, sticky="nsew")

        self.windows_card = InfoCard(grid, "Windows")
        self.windows_card.grid(row=1, column=1, padx=6, pady=6, sticky="nsew")

    def _build_disks_section(self):
        ctk.CTkLabel(self.scroll, text=t("system.disks"), font=ctk.CTkFont(size=16, weight="bold")).pack(
            padx=6, pady=(6, 4), anchor="w"
        )
        self.disks_frame = theme.plain_frame(self.scroll)
        self.disks_frame.pack(fill="x", padx=6, pady=(0, 10))
        self.disks_frame.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(self.disks_frame, text=t("common.loading_dots"), text_color="gray").grid(
            row=0, column=0, sticky="w"
        )

    def _build_monitors_section(self):
        ctk.CTkLabel(self.scroll, text=t("system.monitors"), font=ctk.CTkFont(size=16, weight="bold")).pack(
            padx=6, pady=(6, 4), anchor="w"
        )
        self.monitors_frame = theme.plain_frame(self.scroll)
        self.monitors_frame.pack(fill="x", padx=6, pady=(0, 10))
        self.monitors_frame.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(self.monitors_frame, text=t("common.loading_dots"), text_color="gray").grid(
            row=0, column=0, sticky="w"
        )

    def _build_tips_section(self):
        ctk.CTkLabel(self.scroll, text=t("system.tips"), font=ctk.CTkFont(size=16, weight="bold")).pack(
            padx=6, pady=(6, 4), anchor="w"
        )
        self.tips_frame = theme.plain_frame(self.scroll)
        self.tips_frame.pack(fill="x", padx=6, pady=(0, 10))
        self.tips_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.tips_frame, text=t("common.loading_dots"), text_color="gray").grid(
            row=0, column=0, sticky="w"
        )

    def _build_report_section(self):
        section = ctk.CTkFrame(self.scroll, corner_radius=10)
        section.pack(fill="x", padx=6, pady=(4, 20))

        ctk.CTkLabel(
            section, text=t("system.report.title"), font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(padx=16, pady=(14, 2), anchor="w")
        ctk.CTkLabel(
            section, text=t("system.report.hint"),
            text_color="gray", font=ctk.CTkFont(size=11),
        ).pack(padx=16, pady=(0, 10), anchor="w")

        self.report_start_button = ctk.CTkButton(
            section, text=t("system.report.start"), height=42,
            font=ctk.CTkFont(size=14, weight="bold"), command=self._start_report,
        )
        self.report_start_button.pack(padx=16, pady=(0, 14))

        self.report_bot = CleanerBotAnimation(section, height=170)

        self._build_report_result_section(section)

    def _build_report_result_section(self, master):
        self.report_result_frame = theme.plain_frame(master)

        top = theme.plain_frame(self.report_result_frame)
        top.pack(fill="x", padx=16, pady=(4, 6))
        self.report_badge = ctk.CTkLabel(
            top, text="", font=ctk.CTkFont(size=16, weight="bold"), corner_radius=8,
            width=160, height=30,
        )
        self.report_badge.pack(side="left")

        self.report_stats_label = ctk.CTkLabel(
            self.report_result_frame, text="", font=ctk.CTkFont(size=13), justify="left", anchor="w",
        )
        self.report_stats_label.pack(fill="x", padx=16, pady=(0, 6), anchor="w")

        self.report_offenders_label = ctk.CTkLabel(
            self.report_result_frame, text="", font=ctk.CTkFont(size=12), justify="left",
            anchor="w", text_color="gray",
        )
        self.report_offenders_label.pack(fill="x", padx=16, pady=(0, 8), anchor="w")

        self.report_advice_frame = theme.plain_frame(self.report_result_frame)
        self.report_advice_frame.pack(fill="x", padx=16, pady=(0, 10))

        buttons = theme.plain_frame(self.report_result_frame)
        buttons.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(
            buttons, text=t("system.report.repeat"), width=180, command=self._start_report,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            buttons, text=t("system.report.copy"), width=160, command=self._copy_report,
        ).pack(side="left")

    # ------------------------------------------------------------- снапшот

    def _load_snapshot(self):
        def worker():
            tips = []
            try:
                snapshot = system_info_core.collect_static_snapshot()
                # поради теж тут: вони запускають powercfg (subprocess, ~0.3 с),
                # що в UI-потоці підвішувало б інтерфейс
                tips = system_info_core.build_smart_tips(snapshot)
            except Exception:
                _logger.exception("Failed to collect system information")
                snapshot = None
            bg.ui_call(self, self._apply_snapshot, snapshot, tips)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_snapshot(self, snapshot, tips):
        if not self.winfo_exists():
            return

        if snapshot is None:
            for card in (self.cpu_card, self.gpu_card, self.ram_card, self.windows_card):
                card.set_error(t("system.load_failed"))
            return

        self._snapshot = snapshot
        self._render_hardware(snapshot)
        self._render_disks(snapshot["disks"])
        self._render_monitors(snapshot["monitors"])
        self._render_tips(tips)

    @staticmethod
    def _cpu_lines(cpu: dict) -> list[str]:
        core_bits = []
        if cpu.get("cores_physical"):
            core_bits.append(t("sysinfo.cores", count=cpu['cores_physical']))
        if cpu.get("cores_logical"):
            core_bits.append(t("sysinfo.threads", count=cpu['cores_logical']))
        lines = [cpu["model"]]
        if core_bits:
            lines.append(" / ".join(core_bits))
        freq = system_info_core.format_cpu_freq(cpu.get("freq_ghz"), cpu.get("current_ghz"))
        if freq:
            lines.append(freq)
        return lines

    def _render_hardware(self, snapshot):
        cpu = snapshot["cpu"]
        cpu["current_ghz"] = system_info_core.current_cpu_freq_ghz(self._monitor_snapshot())
        self.cpu_card.set_lines(self._cpu_lines(cpu))

        gpu = snapshot["gpu"]
        gpu_lines = [gpu["model"]]
        if gpu.get("memory_mb"):
            gpu_lines.append(t("system.gpu_mem", gb=gpu['memory_mb'] / 1024))
        driver_line = t("system.gpu_driver", driver_version=gpu['driver_version'])
        if gpu.get("driver_date"):
            driver_line += f" ({gpu['driver_date'].strftime('%d.%m.%Y')})"
        gpu_lines.append(driver_line)
        self.gpu_card.set_lines(gpu_lines)

        ram = snapshot["ram"]
        ram_lines = [t("system.ram_total", total_gb=ram['total_gb'])]
        modules = system_info_core.format_ram_modules(ram)
        if modules:
            ram_lines.append(t("system.ram_modules", modules=modules))
        speed = system_info_core.format_ram_speed(ram)
        if speed:
            ram_lines.append(t("system.ram_speed", speed=speed))
        channels = system_info_core.format_ram_channels(ram)
        if channels:
            ram_lines.append(channels)
        ram_lines.append(t("system.ram_used", used_gb=ram['used_gb'], percent=ram['percent'], free_gb=ram['free_gb']))
        self.ram_card.set_lines(ram_lines)

        windows = snapshot["windows"]
        self.windows_card.set_lines([
            t("system.windows_build", version=windows['version'], build=windows['build']),
            t("system.uptime", uptime_text=windows['uptime_text']),
        ])

    def _render_disks(self, disks):
        for widget in self.disks_frame.winfo_children():
            widget.destroy()

        if not disks:
            ctk.CTkLabel(self.disks_frame, text=t("system.no_disks"), text_color="gray").grid(
                row=0, column=0, sticky="w"
            )
            return

        for i, disk in enumerate(disks):
            DiskCard(self.disks_frame, disk).grid(row=i // 2, column=i % 2, padx=6, pady=6, sticky="nsew")

    def _render_monitors(self, monitors):
        for widget in self.monitors_frame.winfo_children():
            widget.destroy()

        if not monitors:
            ctk.CTkLabel(
                self.monitors_frame, text=t("system.no_monitors"), text_color="gray",
            ).grid(row=0, column=0, sticky="w")
            return

        for i, mon in enumerate(monitors):
            card = InfoCard(self.monitors_frame, mon["name"] or t("sysinfo.monitor_n", n=i + 1))
            freq = t("system.monitor_hz", current_hz=mon['current_hz'])
            if mon["max_hz"]:
                freq += t("system.monitor_max", max_hz=mon['max_hz'])
            card.set_lines([f"{mon['width']}×{mon['height']}", freq])
            card.grid(row=i // 2, column=i % 2, padx=6, pady=6, sticky="nsew")

    def _render_tips(self, tips):
        for widget in self.tips_frame.winfo_children():
            widget.destroy()

        if not tips:
            ctk.CTkLabel(
                self.tips_frame, text=t("system.no_problems"), text_color="#2ee59d",
            ).grid(row=0, column=0, sticky="w")
            return

        for i, tip in enumerate(tips):
            TipCard(self.tips_frame, tip, self._on_tip_fix).grid(
                row=i, column=0, sticky="ew", pady=4
            )

    # --------------------------------------------------------- підказки: дії

    def _on_tip_fix(self, tip):
        action = tip.get("action")
        if action == "open_display_settings":
            self._open_uri("ms-settings:display-advanced")
        elif action == "open_driver_page":
            url = system_info_core.driver_page_url(tip.get("gpu_model", ""))
            if url:
                webbrowser.open(url)
            else:
                self._open_uri("ms-settings:windowsupdate")
        elif action == "open_cleanup_tab":
            self._go_to_tab("cleanup")
        elif action == "open_windows_update":
            self._open_uri("ms-settings:windowsupdate")
        elif action == "switch_to_balanced":
            self._switch_power_plan()
        elif action == "xmp_help":
            messagebox.showinfo("XMP / DOCP / EXPO", t(system_info_core.XMP_HELP_TEXT), parent=self)

    def _open_uri(self, uri: str):
        try:
            os.startfile(uri)
        except OSError:
            messagebox.showerror(t("common.error"), t("system.err.settings"), parent=self)

    def _go_to_tab(self, key: str):
        toplevel = self.winfo_toplevel()
        select_tab = getattr(toplevel, "select_tab", None)
        if callable(select_tab):
            select_tab(key)

    def _switch_power_plan(self):
        def worker():
            success, error = game_mode_core.set_active_power_scheme(
                game_mode_core.POWER_PLANS["balanced"]
            )
            bg.ui_call(self, self._on_power_switch_done, success, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_power_switch_done(self, success, error):
        if not self.winfo_exists():
            return
        if success:
            messagebox.showinfo(t("common.done"), t("system.balanced_on"), parent=self)
            self._load_snapshot()
        else:
            messagebox.showerror(t("common.error"), error or t("game_mode.err.switch_plan"), parent=self)

    # ----------------------------------------------------------------- звіт

    def _start_report(self):
        if self._report_running:
            return
        self._report_running = True

        self.report_start_button.pack_forget()
        self.report_result_frame.pack_forget()
        self.report_bot.pack(fill="x", padx=16, pady=(0, 16))

        self._report_stop_event = threading.Event()
        self._report_started_at = time.monotonic()
        self.report_bot.start(t("system.analyzing", seconds=system_info_core.REPORT_DURATION_SEC), tool="scan")
        self._tick_report()

        stop_event = self._report_stop_event

        def worker():
            report = system_info_core.run_diagnostic(system_info_core.REPORT_DURATION_SEC, stop_event)
            bg.ui_call(self, self._finish_report, report)

        threading.Thread(target=worker, daemon=True).start()

    def _tick_report(self):
        if not self.winfo_exists():
            return

        elapsed = time.monotonic() - self._report_started_at
        duration = system_info_core.REPORT_DURATION_SEC
        remaining = max(0.0, duration - elapsed)
        progress = min(1.0, elapsed / duration)
        self.report_bot.update(t("system.analyzing_left", seconds=remaining), progress)

        if elapsed >= duration:
            self._report_after_id = None
            return
        self._report_after_id = self.after(200, self._tick_report)

    def _finish_report(self, report):
        self._report_running = False
        if not self.winfo_exists():
            return

        success = report["score"] >= 60
        self.report_bot.finish(t("network.done") if success else t("system.found_work"), success=success)

        self._last_report_text = system_info_core.build_report_text(report)
        self._render_report_result(report)

    def _render_report_result(self, report):
        self.report_badge.configure(
            text=f"{report['label']} · {report['score']}/100",
            fg_color=report["color"], text_color="white",
        )

        gpu_text = f"   GPU: {report['gpu_avg']:.0f}%" if report["gpu_avg"] is not None else ""
        self.report_stats_label.configure(
            text=(
                t("system.report.load", cpu_avg=report['cpu_avg'], ram_avg=report['ram_avg'], disk_avg=report['disk_avg'], gpu_text=gpu_text)
            )
        )

        if report["top_offenders"]:
            lines = [t("sysinfo.report.top")]
            for proc in report["top_offenders"]:
                lines.append(f"•  {proc['name']} — CPU {proc['cpu_avg']:.0f}%, RAM {proc['ram_avg']:.0f}%")
            self.report_offenders_label.configure(text="\n".join(lines))
        else:
            self.report_offenders_label.configure(text="")

        for widget in self.report_advice_frame.winfo_children():
            widget.destroy()

        for item in report["advice"]:
            row = theme.plain_frame(self.report_advice_frame)
            row.pack(fill="x", pady=3)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                row, text=f"•  {item['text']}", font=ctk.CTkFont(size=12),
                wraplength=560, justify="left", anchor="w",
            ).grid(row=0, column=0, sticky="w")

            tab_key = item.get("tab_key")
            if tab_key:
                label = (t(system_info_core.ADVICE_TAB_LABELS[tab_key]) if tab_key in system_info_core.ADVICE_TAB_LABELS
                         else t("system.go_to"))
                ctk.CTkButton(
                    row, text=label, width=150, height=26, font=ctk.CTkFont(size=11),
                    command=lambda k=tab_key: self._go_to_tab(k),
                ).grid(row=0, column=1, padx=(10, 0))

        self.report_result_frame.pack(fill="x", pady=(0, 4))

    def _copy_report(self):
        if not self._last_report_text:
            return
        self.clipboard_clear()
        self.clipboard_append(self._last_report_text)

    def _copy_system_info(self):
        if self._snapshot is None:
            messagebox.showinfo(t("common.info"), t("system.still_loading"), parent=self)
            return
        self._snapshot["cpu"]["current_ghz"] = system_info_core.current_cpu_freq_ghz(self._monitor_snapshot())
        text = system_info_core.build_system_info_text(self._snapshot)
        self.clipboard_clear()
        self.clipboard_append(text)

    # -------------------------------------------------------------- misc

    def _on_destroy(self, event):
        if event.widget is not self:
            return
        if self._report_after_id is not None:
            try:
                self.after_cancel(self._report_after_id)
            except tk.TclError:
                pass
            self._report_after_id = None
        if self._freq_after_id is not None:
            try:
                self.after_cancel(self._freq_after_id)
            except tk.TclError:
                pass
            self._freq_after_id = None
        self._report_stop_event.set()

    def is_busy(self) -> bool:
        """Триває операція, яку не можна перервати перебудовою вкладки (зміна мови)."""
        return bool(self._report_running)
