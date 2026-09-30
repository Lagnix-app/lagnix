"""Глобальні гарячі клавіші PulseFPS (працюють, навіть коли вікно сховане або
активна гра) — WinAPI RegisterHotKey без сторонніх бібліотек.

RegisterHotKey прив'язує клавішу до ПОТОКУ, що її зареєстрував: WM_HOTKEY
приходить у чергу повідомлень саме цього потоку. Тому в менеджера власний потік
із циклом GetMessage, і реєстрація/зняття теж виконуються в ньому (через
PostThreadMessage + очікування результату). Колбек on_hotkey(action) кличеться
в цьому потоці — власник сам передає виклик в інтерфейс (ui/bg.py ui_call).

Комбінація зберігається рядком «Ctrl+Shift+G»: модифікатори + назва клавіші з
_KEYS. Клавіша — віртуальний код (VK), а не символ, тож комбінація не залежить
від розкладки (Ctrl+Shift+G = Ctrl+Shift+П в українській)."""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
from ctypes import wintypes

from core.logging_setup import get_logger

_logger = get_logger(__name__)

# власний екземпляр: argtypes не впливають на інші модулі (pystray теж кличе user32),
# а use_last_error дає надійний код помилки RegisterHotKey
_user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
_kernel32 = ctypes.WinDLL("kernel32") if sys.platform == "win32" else None
if _user32 is not None:
    _user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
    _user32.PeekMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                     wintypes.UINT, wintypes.UINT)
    _user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
    _user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.GetKeyState.restype = ctypes.c_short

DEFAULT_HOTKEYS = {
    "game_mode": "Ctrl+Shift+G",
    "overlay": "Ctrl+Shift+O",
    "show_window": "Ctrl+Shift+P",
}

_MOD_ALT, _MOD_CONTROL, _MOD_SHIFT, _MOD_WIN, _MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
_MODIFIERS = (("Ctrl", _MOD_CONTROL), ("Alt", _MOD_ALT), ("Shift", _MOD_SHIFT), ("Win", _MOD_WIN))
_MOD_BY_NAME = {name.lower(): flag for name, flag in _MODIFIERS}

_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_WM_APP_APPLY = 0x8000 + 1
_ERROR_HOTKEY_ALREADY_REGISTERED = 1409

# назва -> VK
_KEYS: dict[str, int] = {chr(c): c for c in range(ord("A"), ord("Z") + 1)}
_KEYS.update({str(d): 0x30 + d for d in range(10)})
_KEYS.update({f"F{n}": 0x6F + n for n in range(1, 25)})
_KEYS.update({f"Num{d}": 0x60 + d for d in range(10)})
_KEYS.update({
    "Space": 0x20, "PageUp": 0x21, "PageDown": 0x22, "End": 0x23, "Home": 0x24,
    "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28, "Insert": 0x2D, "Delete": 0x2E,
    "Pause": 0x13, "ScrollLock": 0x91,
})
_KEY_BY_VK = {vk: name for name, vk in _KEYS.items()}
_KEY_BY_LOWER = {name.lower(): name for name in _KEYS}

# модифікатори як VK (для запису комбінації у «Налаштуваннях»)
VK_MODIFIERS = {0x10: "Shift", 0x11: "Ctrl", 0x12: "Alt", 0x5B: "Win", 0x5C: "Win",
                0xA0: "Shift", 0xA1: "Shift", 0xA2: "Ctrl", 0xA3: "Ctrl", 0xA4: "Alt", 0xA5: "Alt"}


def pressed_modifiers() -> list[str]:
    """Модифікатори, затиснуті в момент поточного повідомлення клавіатури (GetKeyState) —
    для поля запису комбінації; стан Alt/Win у подіях Tk на Windows ненадійний."""
    if _user32 is None:
        return []
    held = []
    for name, vks in (("Ctrl", (0x11,)), ("Alt", (0x12,)), ("Shift", (0x10,)), ("Win", (0x5B, 0x5C))):
        if any(_user32.GetKeyState(vk) & 0x8000 for vk in vks):
            held.append(name)
    return held


class HotkeyError(ValueError):
    pass


def key_name(vk: int) -> str | None:
    """Назва клавіші для VK або None, якщо таку клавішу не можна використати."""
    return _KEY_BY_VK.get(vk)


def format_combo(modifiers: list[str], key: str | None) -> str:
    """Канонічний порядок модифікаторів: Ctrl+Alt+Shift+Win+Клавіша."""
    order = [name for name, _flag in _MODIFIERS if name in modifiers]
    return "+".join(order + ([key] if key else []))


def parse(combo: str) -> tuple[int, int]:
    """«Ctrl+Shift+G» -> (модифікатори MOD_*, VK). HotkeyError — якщо рядок некоректний."""
    parts = [p.strip() for p in (combo or "").split("+") if p.strip()]
    if not parts:
        raise HotkeyError("порожня комбінація")
    mods = 0
    for part in parts[:-1]:
        flag = _MOD_BY_NAME.get(part.lower())
        if flag is None:
            raise HotkeyError(f"невідомий модифікатор «{part}»")
        mods |= flag
    key = _KEY_BY_LOWER.get(parts[-1].lower())
    if key is None:
        raise HotkeyError(f"клавішу «{parts[-1]}» не можна використати")
    if not mods & (_MOD_CONTROL | _MOD_ALT | _MOD_WIN):
        raise HotkeyError("потрібен Ctrl, Alt або Win — інакше клавіша заважатиме друкувати")
    return mods, _KEYS[key]


def validate(combo: str) -> str | None:
    """Текст помилки або None."""
    try:
        parse(combo)
    except HotkeyError as exc:
        return str(exc)
    return None


class HotkeyManager:
    """apply(bindings) — зареєструвати {дія: комбінація} замість попередніх;
    повертає {дія: текст помилки} для тих, що не вдалося (зайняті тощо)."""

    def __init__(self, on_hotkey):
        self._on_hotkey = on_hotkey
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._requests: "queue.SimpleQueue[tuple]" = queue.SimpleQueue()
        self._registered: dict[int, str] = {}  # id -> дія
        self.available = sys.platform == "win32"

    # ----------------------------------------------------------- керування

    def start(self) -> None:
        if not self.available or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="PulseFPS: гарячі клавіші")
        self._thread.start()
        if not self._ready.wait(2.0):
            _logger.error("Потік гарячих клавіш не стартував за 2 с")

    def apply(self, bindings: dict[str, str], timeout: float = 2.0) -> dict[str, str]:
        if not self.available:
            return {action: "гарячі клавіші доступні лише у Windows" for action in bindings}
        if not self._thread_id:
            return {action: "потік гарячих клавіш не запущено" for action in bindings}
        done = threading.Event()
        result: dict[str, str] = {}
        self._requests.put((dict(bindings), result, done))
        _user32.PostThreadMessageW(self._thread_id, _WM_APP_APPLY, 0, 0)
        if not done.wait(timeout):
            _logger.error("Гарячі клавіші: реєстрація не завершилась за %.0f с", timeout)
            return {action: "не вдалося зареєструвати (тайм-аут)" for action in bindings}
        return result

    def stop(self) -> None:
        thread, self._thread = self._thread, None
        if thread is None:
            return
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
        thread.join(timeout=2.0)
        if thread.is_alive():
            _logger.error("Потік гарячих клавіш не завершився за 2 с")

    # ------------------------------------------------------------- потік

    def _run(self) -> None:
        user32 = _user32
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # створює чергу повідомлень потоку
        self._thread_id = _kernel32.GetCurrentThreadId()
        self._ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == _WM_HOTKEY:
                    action = self._registered.get(int(msg.wParam))
                    if action:
                        try:
                            self._on_hotkey(action)
                        except Exception:
                            _logger.exception("Помилка обробника гарячої клавіші «%s»", action)
                elif msg.message == _WM_APP_APPLY:
                    self._drain_requests()
        finally:
            self._unregister_all()
            self._thread_id = 0

    def _drain_requests(self) -> None:
        while True:
            try:
                bindings, result, done = self._requests.get_nowait()
            except queue.Empty:
                return
            try:
                result.update(self._register(bindings))
            except Exception as exc:
                _logger.exception("Гарячі клавіші: помилка реєстрації")
                result.update({action: str(exc) for action in bindings})
            done.set()

    def _unregister_all(self) -> None:
        for hotkey_id in list(self._registered):
            _user32.UnregisterHotKey(None, hotkey_id)
        self._registered.clear()

    def _register(self, bindings: dict[str, str]) -> dict[str, str]:
        self._unregister_all()
        errors: dict[str, str] = {}
        for index, (action, combo) in enumerate(bindings.items(), start=1):
            if not combo:
                continue  # дію вимкнено
            try:
                mods, vk = parse(combo)
            except HotkeyError as exc:
                errors[action] = str(exc)
                continue
            ctypes.set_last_error(0)
            if _user32.RegisterHotKey(None, index, mods | _MOD_NOREPEAT, vk):
                self._registered[index] = action
                continue
            code = ctypes.get_last_error()
            if code == _ERROR_HOTKEY_ALREADY_REGISTERED:
                errors[action] = f"комбінація {combo} уже зайнята іншою програмою або Windows"
            else:
                errors[action] = f"не вдалося зареєструвати {combo} (код {code})"
            _logger.error("Гаряча клавіша «%s» (%s): %s", action, combo, errors[action])
        return errors
