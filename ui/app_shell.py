"""Усе, що працює поза вікном PulseFPS: іконка в треї, глобальні гарячі клавіші,
оверлей поверх ігор і сповіщення Windows. Власник — MainWindow (self.shell).

Потоки: pystray і гарячі клавіші мають власні потоки; їхні колбеки лише
кладуть виклик у чергу ui/bg.py (ui_call) — усі дії виконуються в потоці
інтерфейсу. shutdown() зупиняє обидва потоки й закриває оверлей (ідемпотентно,
викликається при виході й додатково з main.py)."""

from __future__ import annotations

import ctypes
import time
import tkinter as tk

from core import cleanup, process_control
from core import hotkeys as hotkeys_core
from core import tray as tray_core
from core.logging_setup import get_logger
from core.settings import load_settings, update_setting
from ui import bg
from ui import overlay as overlay_view
from ui.widgets import robot as robot_view

_logger = get_logger(__name__)

HOTKEY_LABELS = {
    "game_mode": "Ігровий режим увімк/вимк",
    "overlay": "Оверлей увімк/вимк",
    "show_window": "Показати/сховати PulseFPS",
}
OVERHEAT_COOLDOWN_S = 5 * 60
_TRAY_IMAGE_PX = 64
_CLEANUP_TIMEOUT_S = 10 * 60


class AppShell:
    def __init__(self, window):
        self.window = window
        self.tray = tray_core.TrayIcon(
            actions={
                "open": lambda: bg.ui_call(window, self.show_window),
                "game_mode": lambda: bg.ui_call(window, self.toggle_game_mode),
                "overlay": lambda: bg.ui_call(window, self.toggle_overlay),
                "cleanup": lambda: bg.ui_call(window, self.quick_cleanup),
                "exit": lambda: bg.ui_call(window, window.exit_app),
            },
            # читаються в потоці pystray — лише прості значення, без Tk
            checked={"game_mode": lambda: self._game_active, "overlay": lambda: self._overlay_on},
        )
        self.hotkeys = hotkeys_core.HotkeyManager(
            lambda action: bg.ui_call(window, self._on_hotkey, action))
        self.hotkey_errors: dict[str, str] = {}
        self._game_active = False
        self._overlay_on = bool(load_settings().get("overlay_enabled", False))
        self._overlay: overlay_view.OverlayWindow | None = None
        self._tray_images: dict[str, object] = {}
        self._tray_hint_shown = False
        self._last_overheat: dict[str, float] = {}
        self._cleanup_task = None
        self._stopped = False

    # ------------------------------------------------------------- запуск

    def start(self) -> None:
        self.tray.show(self._tray_image(robot_view.CALM))
        monitor = self.window.tab_frames.get("monitor")
        if monitor is not None and hasattr(monitor, "add_snapshot_listener"):
            monitor.add_snapshot_listener(self._on_snapshot)
        self.hotkeys.start()
        self.apply_hotkeys()
        if self._overlay_on:
            self._show_overlay()

    def shutdown(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self.hotkeys.stop()
        self._hide_overlay()
        self.tray.stop()

    @property
    def tray_available(self) -> bool:
        return tray_core.is_available()

    def _tray_image(self, mood: str):
        if mood not in self._tray_images:
            try:
                self._tray_images[mood] = robot_view.render_robot(_TRAY_IMAGE_PX, mood)
            except Exception:
                _logger.exception("Не вдалося намалювати робота для трею")
                return None
        return self._tray_images[mood]

    # -------------------------------------------------------------- вікно

    def window_shown(self) -> bool:
        try:
            return self.window.state() not in ("withdrawn", "iconic")
        except tk.TclError:
            return False

    def show_window(self, tab: str | None = None) -> None:
        w = self.window
        w.deiconify()
        w.lift()
        # з гарячої клавіші поверх гри: коротко «поверх усіх», щоб вікно точно вийшло наперед
        w.attributes("-topmost", True)
        w.after(300, lambda: w.attributes("-topmost", False))
        w.focus_force()
        if tab is not None:
            w.select_tab(tab)

    def hide_window(self) -> None:
        """У трей (якщо він є), інакше — просто згорнути."""
        if not self.tray.visible:
            self.window.iconify()
            return
        self.window.withdraw()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.notify("PulseFPS працює у треї",
                             "Подвійний клік по іконці — відкрити вікно, правий клік — меню.")

    def toggle_window(self) -> None:
        """Сховати, лише якщо вікно на передньому плані; перекрите грою — вивести наперед."""
        if self.window_shown() and self._window_in_front():
            self.hide_window()
        else:
            self.show_window()

    def _window_in_front(self) -> bool:
        try:
            return ctypes.windll.user32.GetForegroundWindow() == int(self.window.wm_frame(), 16)
        except (tk.TclError, ValueError, AttributeError, OSError):
            return True

    # ------------------------------------------------------ ігровий режим

    def toggle_game_mode(self) -> None:
        tab = self.window.tab_frames.get("game_mode")
        if tab is not None:
            tab.toggle_from_shortcut()

    def on_game_mode_changed(self, active: bool) -> None:
        self._game_active = active
        image = self._tray_image(robot_view.GAMING if active else robot_view.CALM)
        if image is not None:
            self.tray.set_image(image)
        self.tray.refresh_menu()

    def notify_game_mode_auto(self, game_name: str) -> None:
        if not load_settings().get("notify_game_mode", True):
            return
        self.tray.notify(f"Ігровий режим увімкнено: {game_name}" if game_name else "Ігровий режим увімкнено",
                         "План живлення перемкнено на ігровий. Програми не закривались.")

    # ------------------------------------------------------------- оверлей

    @property
    def overlay_enabled(self) -> bool:
        return self._overlay_on

    def overlay_config(self) -> dict:
        s = load_settings()
        return {
            "size": s.get("overlay_size", "small"),
            "opacity": s.get("overlay_opacity", 0.85),
            "metrics": {**overlay_view.DEFAULT_METRICS, **(s.get("overlay_metrics") or {})},
            "corner": s.get("overlay_corner", "top_left"),
            "position": s.get("overlay_position"),
        }

    def toggle_overlay(self) -> None:
        self.set_overlay_enabled(not self._overlay_on)

    def set_overlay_enabled(self, enabled: bool) -> None:
        self._overlay_on = enabled
        update_setting("overlay_enabled", enabled)
        if enabled:
            self._show_overlay()
        else:
            self._hide_overlay()
        self.tray.refresh_menu()
        settings_tab = self.window.tab_frames.get("settings")
        hook = getattr(settings_tab, "sync_overlay_enabled", None)
        if hook is not None:
            hook(enabled)

    def apply_overlay_settings(self, **overrides) -> None:
        """Після зміни розміру/прозорості/показників/кута в «Налаштуваннях»;
        overrides — ще не збережені значення (повзунок під час перетягування)."""
        if self._overlay is not None:
            self._overlay.configure_overlay({**self.overlay_config(), **overrides})

    def _show_overlay(self) -> None:
        if self._overlay is not None:
            return
        try:
            self._overlay = overlay_view.OverlayWindow(self.window, self.overlay_config(), self._on_overlay_moved)
        except Exception:
            _logger.exception("Не вдалося показати оверлей")
            self._overlay = None
            return
        monitor = self.window.tab_frames.get("monitor")
        latest = monitor.latest_snapshot() if monitor is not None else None
        if latest is not None:
            self._overlay.update_data(latest)

    def _hide_overlay(self) -> None:
        overlay, self._overlay = self._overlay, None
        if overlay is not None:
            overlay.close()

    def _on_overlay_moved(self, x: int, y: int) -> None:
        update_setting("overlay_position", [x, y])

    # ----------------------------------------------- зрізи: оверлей і перегрів

    def _on_snapshot(self, data: dict) -> None:
        if self._overlay is not None:
            self._overlay.update_data(data)
        self._check_overheat(data)

    def _check_overheat(self, data: dict) -> None:
        settings = load_settings()
        if not settings.get("notify_overheat", True):
            return
        threshold = data.get("temp_threshold", settings.get("temp_threshold_c", 85))
        gpu = data.get("gpu") or {}
        now = time.monotonic()
        for kind, temp in (("CPU", data.get("cpu_temp")), ("GPU", gpu.get("temperature_c"))):
            if temp is None or temp <= threshold:
                continue
            last = self._last_overheat.get(kind)
            if last is not None and now - last < OVERHEAT_COOLDOWN_S:
                continue
            self._last_overheat[kind] = now
            self.tray.notify(f"Перегрів {kind}: {temp:.0f}°C",
                             f"Поріг — {threshold}°C. Перевірте охолодження й навантаження "
                             f"(поріг змінюється в «Налаштуваннях»).")

    # ------------------------------------------------------ гарячі клавіші

    def hotkey_bindings(self) -> dict[str, str]:
        saved = load_settings().get("hotkeys") or {}
        return {action: saved.get(action, default) for action, default in hotkeys_core.DEFAULT_HOTKEYS.items()}

    def apply_hotkeys(self) -> dict[str, str]:
        self.hotkey_errors = self.hotkeys.apply(self.hotkey_bindings())
        return self.hotkey_errors

    def suspend_hotkeys(self) -> None:
        """На час запису нової комбінації: зареєстровані клавіші Windows «з'їдає»
        до того, як їх побачить поле запису."""
        self.hotkeys.apply({})

    def set_hotkey(self, action: str, combo: str) -> str | None:
        """Зберегти комбінацію для дії; текст помилки — якщо не вийшло (стара лишається)."""
        error = hotkeys_core.validate(combo)
        if error:
            return error
        bindings = self.hotkey_bindings()
        for other, other_combo in bindings.items():
            if other != action and other_combo and other_combo.lower() == combo.lower():
                return f"{combo} уже призначено для «{HOTKEY_LABELS.get(other, other)}»"
        wanted = dict(bindings, **{action: combo})
        errors = self.hotkeys.apply(wanted)
        if action in errors:
            self.apply_hotkeys()  # повернути попередній набір
            return errors[action]
        self.hotkey_errors = errors
        update_setting("hotkeys", wanted)
        return None

    def reset_hotkey(self, action: str) -> str | None:
        return self.set_hotkey(action, hotkeys_core.DEFAULT_HOTKEYS[action])

    def _on_hotkey(self, action: str) -> None:
        if action == "game_mode":
            self.toggle_game_mode()
        elif action == "overlay":
            self.toggle_overlay()
        elif action == "show_window":
            self.toggle_window()

    # --------------------------------------------------- швидке очищення

    def quick_cleanup(self) -> None:
        """Трей → «Швидке очищення тимчасових файлів»: %TEMP% і Windows\\Temp.
        Видалення — лише після підтвердження (core/process_control.ask_user_action)."""
        if self._cleanup_task is not None and not self._cleanup_task.finished:
            self.tray.notify("Очищення вже триває", "Дочекайтеся завершення попереднього очищення.")
            return
        targets = [t for t in cleanup.get_targets() if t.get("category") == cleanup.CAT_TEMP]
        if not targets:
            return
        host = self._dialog_host()
        try:
            action = process_control.ask_user_action(
                host, "Швидке очищення",
                "Видалити тимчасові файли?\n\n" + "\n".join("• " + t["label"] for t in targets)
                + "\n\nФайли, які зараз використовуються, буде пропущено.",
                reason="Трей → «Швидке очищення тимчасових файлів»",
            )
        finally:
            if host is not self.window:
                host.destroy()
        if action is None:
            return
        keys = [t["key"] for t in targets]
        self._cleanup_task = bg.run_task(
            self.window, "Трей: швидке очищення", lambda: cleanup.clean_many(keys, action),
            lambda result: self._on_cleanup_done(keys, result), self._on_cleanup_failed,
            timeout=_CLEANUP_TIMEOUT_S,
        )

    def _on_cleanup_done(self, keys: list[str], result: dict) -> None:
        text = (f"Звільнено {cleanup.format_size(result['freed_bytes'])}, "
                f"видалено файлів: {result['deleted_count']}")
        if result["skipped_count"]:
            text += f", пропущено (зайняті): {result['skipped_count']}"
        self.tray.notify("Тимчасові файли очищено", text)
        hook = getattr(self.window.tab_frames.get("cleanup"), "rescan_targets", None)
        if hook is not None:
            hook(keys)

    def _on_cleanup_failed(self, exc: BaseException) -> None:
        self.tray.notify("Не вдалося очистити тимчасові файли", bg.error_text(exc))

    def _dialog_host(self):
        """Батько для діалогу: саме вікно, якщо воно видиме; інакше — невидиме вікно
        поверх усіх по центру екрана (діалог від схованого вікна міг би опинитись позаду)."""
        if self.window_shown():
            self.window.lift()
            return self.window
        host = tk.Toplevel(self.window)
        host.overrideredirect(True)
        host.attributes("-alpha", 0.0)
        host.attributes("-topmost", True)
        host.geometry(f"1x1+{host.winfo_screenwidth() // 2}+{host.winfo_screenheight() // 3}")
        host.update_idletasks()
        host.lift()
        host.focus_force()
        return host
