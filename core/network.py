"""Пінг хостів для вкладки «Мережа»: одноразовий ICMP-пінг, фоновий воркер, статистика."""

import ctypes
import platform
import re
import subprocess
import threading
from collections import deque
from statistics import mean

try:
    from icmplib import ping as _icmp_ping
    from icmplib import ICMPLibError, SocketPermissionError
except ImportError:
    _icmp_ping = None
    ICMPLibError = SocketPermissionError = Exception

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_IS_WINDOWS = platform.system() == "Windows"

# Захоплює підпис часу різними мовами Windows/Unix ping:
# "time=15ms", "время=14мс", "час=14мс", "time<1ms", "время<1мс" тощо.
_TIME_RE = re.compile(
    r"(?:time|время|час)\s*[=<]\s*(\d+(?:[.,]\d+)?)\s*(?:ms|мс)",
    re.IGNORECASE,
)

DEFAULT_INTERVAL_SEC = 1.0
DEFAULT_TIMEOUT_MS = 1000
HISTORY_SIZE = 60

# icmplib недоступний (не встановлений або немає прав) — вимикаємо на весь
# процес після першої невдачі, щоб не намагатись на кожному пінгу.
_icmp_state = {"available": _icmp_ping is not None}


def _console_encoding() -> str:
    """Кодова сторінка консолі OEM, у якій Windows виводить текст утиліт типу ping."""
    if _IS_WINDOWS:
        try:
            return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        except Exception:
            return "cp866"
    return "utf-8"


def _ping_via_icmplib(host: str, timeout_ms: int) -> float | None:
    result = _icmp_ping(host, count=1, timeout=timeout_ms / 1000, privileged=False)
    return result.avg_rtt if result.is_alive else None


def _ping_via_system(host: str, timeout_ms: int) -> float | None:
    if _IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), host]

    try:
        raw_bytes = subprocess.check_output(
            cmd,
            stderr=subprocess.STDOUT,
            timeout=(timeout_ms / 1000) + 1,
            creationflags=_NO_WINDOW,
        )
    except (subprocess.SubprocessError, OSError):
        return None

    raw = raw_bytes.decode(_console_encoding(), errors="replace")

    match = _TIME_RE.search(raw)
    if match:
        return float(match.group(1).replace(",", "."))
    return None


def ping_once(host: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> float | None:
    """Виконує один ICMP-пінг хосту. Повертає RTT у мс або None при таймауті/помилці.

    Спершу пробує icmplib (без прав адміністратора), а якщо він недоступний
    у цій системі — переходить на системну утиліту ping.
    """
    host = host.strip()
    if not host:
        return None

    if _icmp_state["available"]:
        try:
            return _ping_via_icmplib(host, timeout_ms)
        except SocketPermissionError:
            _icmp_state["available"] = False
        except ICMPLibError:
            return None

    return _ping_via_system(host, timeout_ms)


def compute_stats(history: list) -> dict:
    """Середній пінг, джитер (середня різниця між сусідніми успішними пінгами) і % втрат."""
    total = len(history)
    successes = [v for v in history if v is not None]

    if not successes:
        return {"avg": None, "jitter": None, "loss_percent": 100.0 if total else 0.0}

    avg = mean(successes)
    diffs = [abs(successes[i] - successes[i - 1]) for i in range(1, len(successes))]
    jitter = mean(diffs) if diffs else 0.0
    loss_percent = (total - len(successes)) / total * 100

    return {"avg": avg, "jitter": jitter, "loss_percent": loss_percent}


class PingWorker:
    """Періодично пінгує хост у фоновому потоці й повідомляє про кожен результат через callback."""

    def __init__(
        self,
        host: str,
        on_update,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        history_size: int = HISTORY_SIZE,
    ):
        self.host = host
        self._on_update = on_update
        self._interval = interval_sec
        self._timeout_ms = timeout_ms
        self._history = deque(maxlen=history_size)
        self._stop_event = threading.Event()
        self._thread = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            latency = ping_once(self.host, self._timeout_ms)

            if self._stop_event.is_set():
                break

            self._history.append(latency)
            stats = compute_stats(list(self._history))
            self._on_update(self.host, latency, stats)

            self._stop_event.wait(self._interval)
