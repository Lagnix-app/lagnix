"""Іконки програм: витягування з exe/dll/ico через Win32 (ctypes, без pywin32).

`extract_icon` повертає PIL-зображення RGBA або None. `IconLoader` вантажить
іконки в одному фоновому потоці й кешує результат (у тому числі "іконки немає"),
тож прокрутка списку не робить повторних звернень до диска.
"""

from __future__ import annotations

import ctypes
import os
import queue
import re
import threading
from ctypes import wintypes

from PIL import Image

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
_shell32 = ctypes.windll.shell32

_HANDLE = ctypes.c_void_p

_shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(_HANDLE),
                                    ctypes.POINTER(_HANDLE), wintypes.UINT]
_shell32.ExtractIconExW.restype = wintypes.UINT
_user32.DestroyIcon.argtypes = [_HANDLE]
_user32.GetDC.argtypes = [_HANDLE]
_user32.GetDC.restype = _HANDLE
_user32.ReleaseDC.argtypes = [_HANDLE, _HANDLE]
_gdi32.DeleteObject.argtypes = [_HANDLE]
_gdi32.GetObjectW.argtypes = [_HANDLE, ctypes.c_int, ctypes.c_void_p]
_gdi32.GetDIBits.argtypes = [_HANDLE, _HANDLE, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.UINT]


class _ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", _HANDLE), ("hbmColor", _HANDLE)]


class _BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
                ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD),
                ("bmBitsPixel", wintypes.WORD), ("bmBits", ctypes.c_void_p)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def _bitmap_bgra(hdc, hbitmap, width: int, height: int) -> bytes:
    header = _BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(header)
    header.biWidth = width
    header.biHeight = -height  # зверху вниз
    header.biPlanes = 1
    header.biBitCount = 32
    buf = ctypes.create_string_buffer(width * height * 4)
    _gdi32.GetDIBits(hdc, hbitmap, 0, height, buf, ctypes.byref(header), 0)
    return buf.raw


def _hicon_to_image(hicon) -> Image.Image | None:
    info = _ICONINFO()
    if not _user32.GetIconInfo(hicon, ctypes.byref(info)):
        return None
    hdc = _user32.GetDC(None)
    try:
        if not info.hbmColor:
            return None  # монохромна іконка — рідкість, не підтримуємо
        bmp = _BITMAP()
        _gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(bmp), ctypes.byref(bmp))
        w, h = bmp.bmWidth, bmp.bmHeight
        if w <= 0 or h <= 0:
            return None

        img = Image.frombuffer("RGBA", (w, h), _bitmap_bgra(hdc, info.hbmColor, w, h), "raw", "BGRA", 0, 1)
        if img.getchannel("A").getextrema() == (0, 0):
            # старий формат без альфа-каналу: прозорість задає маска (чорне = видиме)
            mask = Image.frombuffer("RGBA", (w, h), _bitmap_bgra(hdc, info.hbmMask, w, h), "raw", "BGRA", 0, 1)
            img.putalpha(mask.getchannel("R").point(lambda v: 255 if v == 0 else 0))
        return img
    finally:
        _user32.ReleaseDC(None, hdc)
        if info.hbmColor:
            _gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:
            _gdi32.DeleteObject(info.hbmMask)


def extract_icon(path: str, index: int = 0) -> Image.Image | None:
    large, small = _HANDLE(), _HANDLE()
    try:
        count = _shell32.ExtractIconExW(path, index, ctypes.byref(large), ctypes.byref(small), 1)
    except OSError:
        return None
    try:
        if count and large.value:
            return _hicon_to_image(large)
        if count and small.value:
            return _hicon_to_image(small)
        return None
    finally:
        for handle in (large, small):
            if handle.value:
                _user32.DestroyIcon(handle)


_SKIP_EXE_RE = re.compile(r"unins|uninstall|setup|crash|redist|helper|update|report|vcredist", re.IGNORECASE)


def _guess_exe(folder: str, name: str) -> str | None:
    """Головний exe у корені теки: за збігом з назвою, інакше найбільший."""
    try:
        exes = [f for f in os.listdir(folder) if f.lower().endswith(".exe") and not _SKIP_EXE_RE.search(f)]
    except OSError:
        return None
    if not exes:
        return None
    words = [w for w in re.split(r"\W+", name.lower()) if len(w) > 2]
    for exe in exes:
        if any(w in exe.lower() for w in words):
            return os.path.join(folder, exe)
    try:
        return os.path.join(folder, max(exes, key=lambda f: os.path.getsize(os.path.join(folder, f))))
    except OSError:
        return None


def load_program_icon(program: dict) -> Image.Image | None:
    """DisplayIcon -> головний exe теки встановлення -> exe з UninstallString."""
    display_icon = program.get("display_icon")
    if display_icon:
        img = extract_icon(*display_icon)
        if img is not None:
            return img

    folder = program.get("install_folder")
    if folder:
        exe = _guess_exe(folder, program["name"])
        if exe:
            img = extract_icon(exe)
            if img is not None:
                return img

    uninstall = program.get("uninstall_string", "").strip()
    exe_path = uninstall[1:uninstall.find('"', 1)] if uninstall.startswith('"') and '"' in uninstall[1:] else ""
    if exe_path.lower().endswith(".exe") and os.path.basename(exe_path).lower() != "msiexec.exe":
        return extract_icon(exe_path)
    return None


class IconLoader:
    """Черга завантаження іконок в одному фоновому потоці + кеш ключ -> зображення/None.

    on_ready(key) викликається з фонового потоку — виклик має сам перейти в потік UI.
    """

    _MISSING = object()

    def __init__(self, on_ready):
        self._on_ready = on_ready
        self._cache: dict[str, Image.Image | None] = {}
        self._queued: set[str] = set()
        self._queue: queue.LifoQueue = queue.LifoQueue()  # найновіші (видимі) запити — першими
        self._thread: threading.Thread | None = None

    def get(self, key: str):
        """(готово, зображення|None); готово=False — іконку ще вантажимо/не запитували."""
        if key in self._cache:
            return True, self._cache[key]
        return False, None

    def request(self, program: dict) -> None:
        key = program["key"]
        if key in self._cache or key in self._queued:
            return
        self._queued.add(key)
        self._queue.put(program)
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            program = self._queue.get()
            try:
                img = load_program_icon(program)
            except Exception:
                img = None
            self._cache[program["key"]] = img
            self._queued.discard(program["key"])
            self._on_ready(program["key"])
