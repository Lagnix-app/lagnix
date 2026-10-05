"""Лічильники продуктивності Windows (PDH) через ctypes — ті самі, що читає
Диспетчер завдань: завантаження GPU («GPU Engine») і поточна частота CPU
(«% Processor Performance»). Без зовнішніх залежностей (pywin32 не потрібен).

Усі виклики PDH виконуються під одним замком: запит PDH не потокобезпечний.
"""

from __future__ import annotations

import ctypes
import re
import sys
import threading
from ctypes import wintypes

_IS_WINDOWS = sys.platform == "win32"

PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA = 0x800007D2
_ERROR_SUCCESS = 0


class _CounterValue(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


class _CounterValueItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _CounterValue)]


_ENGINE_RE = re.compile(r"luid_(0x[0-9a-fA-F]+_0x[0-9a-fA-F]+)_phys_\d+_eng_(\d+)_engtype_(\w+)")
_LUID_RE = re.compile(r"luid_(0x[0-9a-fA-F]+_0x[0-9a-fA-F]+)")


class PdhQuery:
    """Запит PDH із підтримкою лічильників-масивів (шаблон `*` в інстансах)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pdh = ctypes.windll.pdh
        self._query = wintypes.HANDLE()
        if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != _ERROR_SUCCESS:
            raise OSError("PdhOpenQuery failed")
        self._counters: dict[str, wintypes.HANDLE] = {}

    def add(self, path: str) -> bool:
        with self._lock:
            handle = wintypes.HANDLE()
            # English-версія шляху — не залежить від мови Windows
            if self._pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(handle)) != _ERROR_SUCCESS:
                return False
            self._counters[path] = handle
            return True

    def collect(self) -> bool:
        with self._lock:
            return self._pdh.PdhCollectQueryData(self._query) == _ERROR_SUCCESS

    def values(self, path: str) -> dict[str, float]:
        """Значення лічильника-масиву: {ім'я інстансу: значення}."""
        handle = self._counters.get(path)
        if handle is None:
            return {}
        with self._lock:
            size = wintypes.DWORD(0)
            count = wintypes.DWORD(0)
            status = self._pdh.PdhGetFormattedCounterArrayW(
                handle, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None,
            )
            if status & 0xFFFFFFFF != PDH_MORE_DATA or size.value == 0:
                return {}
            buf = ctypes.create_string_buffer(size.value)
            status = self._pdh.PdhGetFormattedCounterArrayW(
                handle, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buf,
            )
            if status != _ERROR_SUCCESS:
                return {}
            # from_address, а не ctypes.cast: cast лишає цикл посилань на буфер (~90 КБ щосекунди
            # накопичувались до повного GC — «пилка» пам'яті й паузи збирача)
            base = ctypes.addressof(buf)
            step = ctypes.sizeof(_CounterValueItem)
            result = {}
            for i in range(count.value):
                item = _CounterValueItem.from_address(base + i * step)
                if item.FmtValue.CStatus in (0, 0x00000001):
                    result[item.szName] = item.FmtValue.doubleValue
            return result


_query: PdhQuery | None = None
_init_lock = threading.Lock()
_init_failed = False
_paths: dict[str, bool] = {}

_GPU_ENGINE = r"\GPU Engine(*)\Utilization Percentage"
_GPU_MEM = r"\GPU Adapter Memory(*)\Dedicated Usage"
_CPU_PERF = r"\Processor Information(_Total)\% Processor Performance"


def _get_query() -> PdhQuery | None:
    global _query, _init_failed
    if not _IS_WINDOWS or _init_failed:
        return None
    with _init_lock:
        if _query is None and not _init_failed:
            try:
                q = PdhQuery()
                for path in (_GPU_ENGINE, _GPU_MEM, _CPU_PERF):
                    _paths[path] = q.add(path)
                _query = q
                q.collect()  # перший зріз; значення з'являються з другого
            except Exception:
                _init_failed = True
                return None
    return _query


def prime() -> None:
    _get_query()


def sample() -> dict:
    """Один зріз: {"gpu_load": % | None, "cpu_perf_percent": % | None,
    "gpu_luid": str | None}. Перший виклик після prime() може дати None."""
    q = _get_query()
    result = {"gpu_load": None, "cpu_perf_percent": None, "gpu_luid": None}
    if q is None or not q.collect():
        return result

    if _paths.get(_CPU_PERF):
        vals = q.values(_CPU_PERF)
        if vals:
            result["cpu_perf_percent"] = next(iter(vals.values()))

    if _paths.get(_GPU_ENGINE):
        # Як у Диспетчері завдань: завантаження рушія = сума по всіх процесах на
        # цьому рушії; показник GPU = максимум по рушіях 3D. Адаптер обираємо
        # за найбільшим використанням виділеної відеопам'яті (дискретна карта).
        engines: dict[tuple[str, str], float] = {}
        for name, value in q.values(_GPU_ENGINE).items():
            m = _ENGINE_RE.search(name)
            if m and m.group(3).lower() == "3d":
                key = (m.group(1), m.group(2))
                engines[key] = engines.get(key, 0.0) + value

        luid = _pick_adapter(q, {k[0] for k in engines})
        if luid is not None:
            result["gpu_luid"] = luid
            result["gpu_load"] = min(100.0, max((v for (l, _e), v in engines.items() if l == luid), default=0.0))
    return result


def _pick_adapter(q: PdhQuery, candidates: set[str]) -> str | None:
    if not candidates:
        return None
    if len(candidates) == 1 or not _paths.get(_GPU_MEM):
        return sorted(candidates)[0]
    usage: dict[str, float] = {}
    for name, value in q.values(_GPU_MEM).items():
        m = _LUID_RE.search(name)
        if m:
            usage[m.group(1)] = usage.get(m.group(1), 0.0) + value
    return max(candidates, key=lambda luid: usage.get(luid, 0.0))
