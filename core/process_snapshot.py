"""Швидкий знімок усіх процесів Windows одним системним викликом.

psutil.process_iter(["cpu_percent", "memory_info"]) для кожного процесу
відкриває його окремо, а для процесів, до яких немає доступу (системні,
запущені від адміністратора), щоразу перелічує ВСІ процеси системи
(proc_info -> NtQuerySystemInformation) — O(n²): ~300 мс на знімок, і весь
цей час фоновий потік тримає GIL, тож інтерфейс завмирав щосекунди.

NtQuerySystemInformation(SystemProcessInformation) повертає за один виклик
(ctypes відпускає GIL на час виклику) PID, назву, час CPU й робочий набір
кожного процесу — так само рахує Диспетчер завдань. Відсоток CPU — різниця
часу CPU між двома знімками, поділена на минулий час і кількість ядер.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

_SYSTEM_PROCESS_INFORMATION = 5
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004


class _UnicodeString(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]


class _SystemProcessInformation(ctypes.Structure):
    # Початок структури SYSTEM_PROCESS_INFORMATION (winternl.h / NT API);
    # поля після WorkingSetSize нам не потрібні — NextEntryOffset веде далі.
    _fields_ = [
        ("NextEntryOffset", wintypes.ULONG),
        ("NumberOfThreads", wintypes.ULONG),
        ("WorkingSetPrivateSize", ctypes.c_longlong),
        ("HardFaultCount", wintypes.ULONG),
        ("NumberOfThreadsHighWatermark", wintypes.ULONG),
        ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("KernelTime", ctypes.c_longlong),
        ("ImageName", _UnicodeString),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ("HandleCount", wintypes.ULONG),
        ("SessionId", wintypes.ULONG),
        ("UniqueProcessKey", ctypes.c_void_p),
        ("PeakVirtualSize", ctypes.c_size_t),
        ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", wintypes.ULONG),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
    ]


_available = sys.platform == "win32"
if _available:
    try:
        _ntdll = ctypes.WinDLL("ntdll")
        _query = _ntdll.NtQuerySystemInformation
        _query.argtypes = [wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
        _query.restype = ctypes.c_long
    except (OSError, AttributeError):
        _available = False

_buffer = None
_buffer_size = 512 * 1024
_prev: dict = {}  # (pid, create_time) -> cpu_time_100ns
_prev_time: float | None = None
_CPU_COUNT = os.cpu_count() or 1
_lock = threading.Lock()  # буфер і стан CPU спільні для потоків (Монітор, Ігровий режим)


def is_available() -> bool:
    return _available


def _query_raw():
    global _buffer, _buffer_size
    needed = wintypes.ULONG(0)
    for _attempt in range(4):
        if _buffer is None or len(_buffer) < _buffer_size:
            _buffer = ctypes.create_string_buffer(_buffer_size)
        status = _query(_SYSTEM_PROCESS_INFORMATION, _buffer, _buffer_size, ctypes.byref(needed)) & 0xFFFFFFFF
        if status == _STATUS_INFO_LENGTH_MISMATCH:
            _buffer_size = max(_buffer_size * 2, needed.value + 64 * 1024)
            continue
        if status != 0:
            raise OSError(f"NtQuerySystemInformation: 0x{status:08X}")
        return _buffer
    raise OSError("NtQuerySystemInformation: буфер замалий")


def sample(track_cpu: bool = True) -> list[dict]:
    with _lock:
        return _sample_locked(track_cpu)


def _sample_locked(track_cpu: bool) -> list[dict]:
    """Усі процеси: pid, ppid, name, create_time (FILETIME, 100 нс), cpu_percent
    (0..100 на всю систему) і memory_mb — private working set, тобто те саме, що
    колонка «Пам'ять» у Диспетчері завдань (rss/Working Set рахує ще й спільні
    сторінки DLL, тож для Edge виходить майже вдвічі більше).
    Перший виклик дає cpu_percent = 0. track_cpu=False — «разовий» знімок для
    інших вкладок (назви, RAM): він не чіпає стан, з якого рахується CPU %,
    тож не збиває показники Монітора; cpu_percent у ньому = 0."""
    global _prev, _prev_time
    buf = _query_raw()
    now = time.perf_counter()
    elapsed = None if (_prev_time is None or not track_cpu) else now - _prev_time
    base = ctypes.addressof(buf)
    offset = 0
    current: dict = {}
    out = []
    while True:
        info = _SystemProcessInformation.from_address(base + offset)
        pid = info.UniqueProcessId or 0
        name_len = info.ImageName.Length // 2
        name = ctypes.wstring_at(info.ImageName.Buffer, name_len) if info.ImageName.Buffer and name_len else ""
        cpu_time = info.UserTime + info.KernelTime
        key = (pid, info.CreateTime)
        current[key] = cpu_time
        cpu = 0.0
        if elapsed and key in _prev:
            cpu = (cpu_time - _prev[key]) / (elapsed * 1e7 * _CPU_COUNT) * 100.0
        if pid:
            out.append({
                "pid": pid,
                "ppid": info.InheritedFromUniqueProcessId or 0,
                "create_time": info.CreateTime,
                "name": name or "—",
                "cpu_percent": max(0.0, min(cpu, 100.0)),
                "memory_mb": info.WorkingSetPrivateSize / (1024 ** 2),
            })
        if not info.NextEntryOffset:
            break
        offset += info.NextEntryOffset
    if track_cpu:
        _prev, _prev_time = current, now
    return out
