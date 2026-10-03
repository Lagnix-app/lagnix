"""Пінг хостів для вкладки «Мережа»: одноразовий ICMP-пінг, фоновий воркер, статистика."""

import ctypes
import platform
import re
import subprocess
import threading
from collections import deque
from statistics import mean

from core.app_data import load_data, save_data
from core.i18n import t

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


RATING_LABELS = ("sysinfo.rating.excellent", "sysinfo.rating.good", "sysinfo.rating.fair", "sysinfo.rating.poor")
RATING_COLORS = ("#2ee59d", "#d4b106", "#e0a52f", "#ff5c7a")


def rating_label(level: int) -> str:
    """Підпис оцінки поточною мовою (RATING_LABELS — ключі перекладів)."""
    return t(RATING_LABELS[level])


def entry_level(entry: dict) -> int:
    """Рівень оцінки запису історії (0 — відмінно … 3 — погано) — з його метрик,
    тож показ не залежить від мови, якою підпис колись зберегли в data.json."""
    return max(_ping_level(entry.get("avg")), _jitter_level(entry.get("jitter")),
               _loss_level(entry.get("loss", 0) or 0))


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
        notes.append(t("network.note.ping0"))
    elif ping_level == 1:
        notes.append(t("network.note.ping1"))
    elif ping_level == 2:
        notes.append(t("network.note.ping2"))
    else:
        notes.append(t("network.note.ping3"))

    if jitter_level == 1:
        notes.append(t("network.note.jitter1"))
    elif jitter_level == 3:
        notes.append(t("network.note.jitter3"))

    if loss_level in (1, 2):
        notes.append(t("network.note.loss12"))
    elif loss_level == 3:
        notes.append(t("network.note.loss3"))

    return {
        "level": level,
        "label": rating_label(level),
        "color": RATING_COLORS[level],
        "notes": notes[:3],
    }


def explain_entry_rating(avg: float | None, jitter: float | None, loss_percent: float) -> str:
    """Пояснює, чому тест отримав таку оцінку: називає найгіршу метрику."""
    levels = {
        "ping": _ping_level(avg),
        "jitter": _jitter_level(jitter),
        "loss": _loss_level(loss_percent),
    }
    worst = max(levels, key=lambda k: levels[k])
    level = levels[worst]
    label = rating_label(level)

    if worst == "ping":
        if avg is None:
            return t("network.explain.no_reply", label=label)
        detail = t("network.explain.ping", avg=avg)
        tail = (t("network.explain.ping_tail0"), t("network.explain.ping_tail1"),
                t("network.explain.ping_tail2"), t("network.explain.ping_tail3"))[level]
    elif worst == "jitter":
        if jitter is None:
            return t("network.explain.no_jitter", label=label)
        detail = t("network.explain.jitter", jitter=jitter)
        tail = (t("network.explain.jitter_tail0"), t("network.explain.jitter_tail1"),
                t("network.explain.jitter_tail2"), t("network.explain.jitter_tail3"))[level]
    else:
        detail = t("network.explain.loss", loss_percent=loss_percent)
        tail = (t("network.explain.loss_tail0"), t("network.explain.loss_tail1"),
                t("network.explain.loss_tail2"),
                t("network.explain.loss_tail3"))[level]
    return f"{label}: {detail} — {tail}"


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


def delete_test_result(index: int) -> dict | None:
    """Видаляє запис історії за індексом і повертає його (для «Скасувати»)."""
    data = load_data()
    history = list(data.get(TEST_HISTORY_KEY, []))
    if not 0 <= index < len(history):
        return None
    removed = history.pop(index)
    data[TEST_HISTORY_KEY] = history
    save_data(data)
    return removed


def restore_test_result(index: int, entry: dict) -> list:
    """Повертає раніше видалений запис на його місце."""
    data = load_data()
    history = list(data.get(TEST_HISTORY_KEY, []))
    history.insert(min(max(index, 0), len(history)), entry)
    history = history[:TEST_HISTORY_MAX]
    data[TEST_HISTORY_KEY] = history
    save_data(data)
    return history


def clear_test_history() -> list:
    data = load_data()
    data[TEST_HISTORY_KEY] = []
    save_data(data)
    return []
