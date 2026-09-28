"""Пінг хостів для вкладки «Мережа»: одноразовий ICMP-пінг, фоновий воркер, статистика."""

import ctypes
import platform
import re
import subprocess
import threading
from collections import deque
from statistics import mean

from core.app_data import load_data, save_data

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

TEST_DURATION_SEC = 30
TEST_INTERVAL_SEC = 0.5
TEST_HISTORY_KEY = "network_test_history"
TEST_HISTORY_MAX = 10

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

    @property
    def history(self) -> list:
        return list(self._history)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            latency = ping_once(self.host, self._timeout_ms)

            if self._stop_event.is_set():
                break

            self._history.append(latency)
            stats = compute_stats(list(self._history))
            self._on_update(self.host, latency, stats)

            self._stop_event.wait(self._interval)


# ------------------------------------------------------------- тест мережі

def aggregate_stats(histories: list) -> dict:
    """Об'єднує історії пінгу кількох хостів у підсумкову статистику тесту:
    середній/мін/макс пінг, джитер і % втрат по всіх хостах разом.
    """
    total = sum(len(h) for h in histories)
    successes = [v for h in histories for v in h if v is not None]

    if not successes:
        return {
            "avg": None, "min": None, "max": None,
            "jitter": None, "loss_percent": 100.0 if total else 0.0,
        }

    diffs = []
    for h in histories:
        values = [v for v in h if v is not None]
        diffs.extend(abs(values[i] - values[i - 1]) for i in range(1, len(values)))

    return {
        "avg": mean(successes),
        "min": min(successes),
        "max": max(successes),
        "jitter": mean(diffs) if diffs else 0.0,
        "loss_percent": (total - len(successes)) / total * 100 if total else 0.0,
    }


RATING_LABELS = ("Відмінно", "Добре", "Задовільно", "Погано")
RATING_COLORS = ("#2ee59d", "#d4b106", "#e0a52f", "#ff5c7a")
RATING_COLOR_BY_LABEL = dict(zip(RATING_LABELS, RATING_COLORS))


def _ping_level(avg: float | None) -> int:
    if avg is None:
        return 3
    if avg < 30:
        return 0
    if avg < 60:
        return 1
    if avg < 100:
        return 2
    return 3


def _jitter_level(jitter: float | None) -> int:
    if jitter is None:
        return 3
    if jitter < 5:
        return 0
    if jitter <= 15:
        return 1
    return 3


def _loss_level(loss_percent: float) -> int:
    if loss_percent <= 0:
        return 0
    if loss_percent < 1:
        return 1
    if loss_percent <= 2:
        return 2
    return 3


def rate_test(stats: dict) -> dict:
    """Оцінює якість мережі для онлайн-ігор за підсумковою статистикою тесту:
    рівень (0=відмінно..3=погано) — найгірший з пінгу/джитеру/втрат, і 2-3
    короткі висновки простими словами.
    """
    ping_level = _ping_level(stats["avg"])
    jitter_level = _jitter_level(stats["jitter"])
    loss_level = _loss_level(stats["loss_percent"])
    level = max(ping_level, jitter_level, loss_level)

    notes = []
    if ping_level == 0:
        notes.append("Пінг чудовий — для онлайн-ігор ідеально.")
    elif ping_level == 1:
        notes.append("Пінг цілком прийнятний для більшості онлайн-ігор.")
    elif ping_level == 2:
        notes.append("Пінг трохи високий — у швидких іграх можуть відчуватись затримки.")
    else:
        notes.append("Пінг дуже високий — у динамічних іграх це відчуватиметься як лаги.")

    if jitter_level == 1:
        notes.append("Є невеликі стрибки пінгу — зрідка можливі короткі лаги.")
    elif jitter_level == 3:
        notes.append("Є стрибки пінгу — можливі лаги, спробуй кабель замість Wi-Fi.")

    if loss_level in (1, 2):
        notes.append("Трохи втрачаються пакети — стеж за з'єднанням у важливих матчах.")
    elif loss_level == 3:
        notes.append("Втрачаються пакети — перевір роутер або зверніться до провайдера.")

    return {
        "level": level,
        "label": RATING_LABELS[level],
        "color": RATING_COLORS[level],
        "notes": notes[:3],
    }


def load_test_history() -> list:
    data = load_data()
    return list(data.get(TEST_HISTORY_KEY, []))


def save_test_result(entry: dict) -> list:
    """Додає результат тесту на початок історії та зберігає останні TEST_HISTORY_MAX."""
    data = load_data()
    history = list(data.get(TEST_HISTORY_KEY, []))
    history.insert(0, entry)
    history = history[:TEST_HISTORY_MAX]
    data[TEST_HISTORY_KEY] = history
    save_data(data)
    return history
