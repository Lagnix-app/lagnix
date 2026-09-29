"""Підсумки ігрових сесій: збір навантаження/температур під час гри й історія
останніх 10 сесій (data.json, ключ «game_sessions»)."""

from __future__ import annotations

import time

from core.app_data import load_data, update_data

MAX_SESSIONS = 10
MIN_DURATION_S = 30  # коротші «сесії» (гра впала на старті, хибне спрацювання) не зберігаємо


class SessionTracker:
    """Накопичує зрізи Монітора (CPU/GPU %, температури) поки гра запущена."""

    def __init__(self, game: str, key: str | None):
        self.game = game
        self.key = key
        self.started = time.time()
        self._cpu: list[float] = []
        self._gpu: list[float] = []
        self._cpu_temp_max: float | None = None
        self._gpu_temp_max: float | None = None

    def add_sample(self, snapshot: dict | None) -> None:
        if not snapshot or "cpu_percent" not in snapshot:
            return
        self._cpu.append(float(snapshot["cpu_percent"]))
        gpu = snapshot.get("gpu")
        if gpu:
            if gpu.get("load_percent") is not None:
                self._gpu.append(float(gpu["load_percent"]))
            temp = gpu.get("temperature_c")
            if temp is not None:
                self._gpu_temp_max = temp if self._gpu_temp_max is None else max(self._gpu_temp_max, temp)
        temp = snapshot.get("cpu_temp")
        if temp is not None:
            self._cpu_temp_max = temp if self._cpu_temp_max is None else max(self._cpu_temp_max, temp)

    def finish(self) -> dict | None:
        """Підсумок сесії або None, якщо вона закоротка."""
        ended = time.time()
        duration = ended - self.started
        if duration < MIN_DURATION_S:
            return None

        def avg(values):
            return sum(values) / len(values) if values else None

        return {
            "game": self.game,
            "key": self.key,
            "started": self.started,
            "ended": ended,
            "duration_s": round(duration),
            "cpu_avg": avg(self._cpu),
            "cpu_max": max(self._cpu) if self._cpu else None,
            "gpu_avg": avg(self._gpu),
            "gpu_max": max(self._gpu) if self._gpu else None,
            "cpu_temp_max": self._cpu_temp_max,
            "gpu_temp_max": self._gpu_temp_max,
        }


def load_sessions() -> list[dict]:
    """Історія, нові першими."""
    sessions = load_data().get("game_sessions", [])
    return sessions if isinstance(sessions, list) else []


def add_session(session: dict) -> list[dict]:
    sessions = [session] + load_sessions()
    sessions = sessions[:MAX_SESSIONS]
    update_data("game_sessions", sessions)
    return sessions


def clear_sessions() -> None:
    update_data("game_sessions", [])


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} год {minutes:02d} хв"
    if minutes:
        return f"{minutes} хв {secs:02d} с"
    return f"{secs} с"
