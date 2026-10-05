"""Прихований режим `python main.py --screenshots[=en|uk|all]`: знімки вкладок для README/itch.io.

Вікно 1280x800, вкладки по черзі, знімок вікна (ImageGrab за його клієнтською областю) у
docs/screenshots/<назва>.png (en) і docs/screenshots/uk/<назва>.png. Перед кожним знімком
видимі тексти очищаються від приватних даних: ім'я користувача Windows, C:\\Users\\..., IP, MAC, назва ПК.
Налаштування й дані користувача не чіпаються (core/paths.py дає окрему тимчасову теку)."""

import getpass
import json
import os
import re
import socket
import sys
import time
import tkinter as tk

import customtkinter as ctk

from core import paths

WIDTH, HEIGHT = 1280, 800
OUT_ROOT = os.path.join(paths.RESOURCE_DIR, "docs", "screenshots")

# (вкладка, файл, мін. очікування, с)
SHOTS = (
    ("monitor", "monitor", 64),
    ("game_mode", "game-mode", 4),
    ("network", "network", 4),
    ("cleanup", "cleanup", 4),
    ("programs", "programs", 6),
    ("registry_tweaks", "tweaks", 5),
    ("system", "system", 8),
)

_KEEP_IPS = {"8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1", "9.9.9.9", "208.67.222.222", "208.67.220.220",
             "127.0.0.1", "0.0.0.0", "255.255.255.255", "255.255.255.0"}
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6 = re.compile(r"(?i)(?<![0-9a-f:])(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{0,4}(?![0-9a-f:])(?:%\w+)?")
_MAC = re.compile(r"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b")
_USER_PATH = re.compile(r"(?i)([A-Z]:[\\/]+Users[\\/]+)[^\\/\s\"'<>|]+")
_ip_map: dict[str, str] = {}
_rules: list = []


def _word_rule(names: list[str], neutral: str):
    names = sorted({n for n in names if n and len(n) >= 3}, key=len, reverse=True)
    if not names:
        return None
    return re.compile(r"(?i)(?<!\w)(?:" + "|".join(re.escape(n) for n in names) + r")(?!\w)"), neutral


def _build_rules() -> list:
    users = [getpass.getuser(), os.environ.get("USERNAME", ""),
             os.path.basename(os.path.expanduser("~").rstrip("\\/"))]
    host = socket.gethostname()
    pcs = [os.environ.get("COMPUTERNAME", ""), host, host.split(".")[0]]
    return [r for r in (_word_rule(users, "User"), _word_rule(pcs, "PC")) if r]


def _ip(match: re.Match) -> str:
    ip = match.group(0)
    if ip in _KEEP_IPS or any(int(p) > 255 for p in ip.split(".")):
        return ip
    if ip not in _ip_map:
        _ip_map[ip] = f"192.168.1.{len(_ip_map) + 2}"
    return _ip_map[ip]


def scrub(text: str) -> str:
    if not text:
        return text
    out = _USER_PATH.sub(r"\1User", text)
    for pattern, neutral in _rules:
        out = pattern.sub(neutral, out)
    out = _MAC.sub("00:00:00:00:00:00", out)
    out = _IPV4.sub(_ip, out)
    return _IPV6.sub("fe80::1", out)


def _scrub_widget(w) -> None:
    try:
        if isinstance(w, ctk.CTkBaseClass) and not isinstance(w, (ctk.CTkEntry, ctk.CTkTextbox)):
            try:
                text = w.cget("text")
            except Exception:
                text = None
            if isinstance(text, str):
                new = scrub(text)
                if new != text:
                    w.configure(text=new)
        if isinstance(w, tk.Canvas):
            for item in w.find_all():
                if w.type(item) == "text":
                    text = w.itemcget(item, "text")
                    new = scrub(text)
                    if new != text:
                        w.itemconfigure(item, text=new)
        elif isinstance(w, (tk.Label, tk.Button)):
            text = str(w.cget("text"))
            new = scrub(text)
            if new != text:
                w.configure(text=new)
        elif isinstance(w, tk.Entry):
            text = w.get()
            new = scrub(text)
            if new != text:
                state = str(w.cget("state"))
                w.configure(state="normal")
                w.delete(0, "end")
                w.insert(0, new)
                w.configure(state=state)
        elif isinstance(w, tk.Text):
            text = w.get("1.0", "end-1c")
            new = scrub(text)
            if new != text:
                state = str(w.cget("state"))
                w.configure(state="normal")
                w.delete("1.0", "end")
                w.insert("1.0", new)
                w.configure(state=state)
    except Exception:
        pass


def scrub_tree(root) -> None:
    _scrub_widget(root)
    for child in root.winfo_children():
        scrub_tree(child)


def _client_rect(win) -> tuple[int, int, int, int]:
    """Клієнтська область вікна (без рамки й заголовка) у фізичних пікселях: рівно WIDTH x HEIGHT."""
    import ctypes
    from ctypes import wintypes
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
    rect = wintypes.RECT()
    ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect))
    origin = wintypes.POINT(0, 0)
    ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom


def _capture(win, path: str) -> None:
    from PIL import ImageGrab
    win.update()
    ImageGrab.grab(bbox=_client_rect(win), all_screens=True).save(path)


class Runner:
    def __init__(self, app, outdir: str):
        self.app, self.outdir = app, outdir
        self.index = 0
        self.started = 0.0
        self.saved: list[str] = []
        _rules[:] = _build_rules()
        os.makedirs(outdir, exist_ok=True)

    def start(self) -> None:
        app = self.app
        app.geometry(f"{WIDTH}x{HEIGHT}+80+40")
        app.attributes("-topmost", True)
        app.update()
        app.after(2500, self._begin_tab)

    def _begin_tab(self) -> None:
        if self.index >= len(SHOTS):
            self.app.exit_app()
            return
        self.app.select_tab(SHOTS[self.index][0])
        self.started = time.monotonic()
        self.app.after(1000, self._poll)

    def _poll(self) -> None:
        key, name, min_wait = SHOTS[self.index]
        busy = getattr(self.app.tab_frames[key], "is_busy", None)
        waited = time.monotonic() - self.started
        if waited < min_wait or (callable(busy) and busy() and waited < 40):
            self.app.after(500, self._poll)
            return
        scrub_tree(self.app)
        self.app.update()
        self.app.after(700, lambda: self._shoot(name))

    def _shoot(self, name: str) -> None:
        scrub_tree(self.app)
        path = os.path.join(self.outdir, name + ".png")
        _capture(self.app, path)
        self.saved.append(path)
        self.index += 1
        self.app.after(300, self._begin_tab)


def seed_settings(lang: str) -> None:
    """Свіжі налаштування для знімків: потрібна мова, розмір вікна, без звуку й оверлею."""
    data = {"language": lang, "sounds_enabled": False, "overlay_enabled": False,
            "startup_tab_mode": "monitor", "window": {"width": WIDTH, "height": HEIGHT}}
    with open(paths.user_file("settings.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        os.remove(paths.user_file("data.json"))
    except OSError:
        pass


def language_arg() -> str | None:
    for arg in sys.argv[1:]:
        if arg == "--screenshots":
            return "all"
        if arg.startswith("--screenshots="):
            return arg.partition("=")[2] or "all"
    return None


def outdir_for(lang: str) -> str:
    return OUT_ROOT if lang == "en" else os.path.join(OUT_ROOT, lang)
