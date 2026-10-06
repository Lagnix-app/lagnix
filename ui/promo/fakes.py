"""Імітований «комп'ютер» для промо-відео: вигадані процеси, RAM, CPU, GPU.

Жодних справжніх даних користувача: ні процесів, ні імені ПК, ні шляхів. Підміняється лише джерело
даних Монітора (`core.monitor`) — сам інтерфейс той самий, що й у програмі. Закриття програм —
тільки зміна цього вигаданого списку, нічого реального не завершується."""

from __future__ import annotations

import math

from core import monitor as monitor_core
from core import process_groups

TOTAL_GB = 16.0
OTHER_GB = 6.5                 # ядро, драйвери, кеш: не прив'язано до процесів
GPU_NAME = "NVIDIA GeForce RTX 3060"
CREATE_TIME = 1_700_000_000.0
RELEASE_DELAY_S = 0.6          # пам'ять звільняється не миттєво
RELEASE_S = 5.0

# (exe, назва групи, [(пам'ять МБ, cpu %)] — перший процес кореневий)
_APPS = (
    ("services.exe", "Services", [(18, 0.0)]),
    ("svchost.exe", "Host Process", [(310, 0.4)]),
    ("svchost.exe", "Host Process", [(240, 0.2)]),
    ("svchost.exe", "Host Process", [(190, 0.1)]),
    ("dwm.exe", "Desktop Window Manager", [(430, 1.1)]),
    ("explorer.exe", "Windows Explorer", [(360, 0.5)]),
    ("MsMpEng.exe", "Antimalware Service", [(620, 0.8)]),
    ("SearchHost.exe", "Search", [(230, 0.0)]),
    ("Code.exe", "Visual Studio Code", [(280, 0.7), (420, 0.5), (360, 0.4), (260, 0.2), (210, 0.1)]),
    ("chrome.exe", "Google Chrome",
     [(310, 2.4), (520, 1.6), (480, 1.4), (410, 1.1), (360, 0.9), (330, 0.8), (290, 0.7), (230, 0.5), (150, 0.2)]),
    ("Discord.exe", "Discord", [(210, 1.2), (190, 0.9), (95, 0.4), (60, 0.2)]),
    ("steam.exe", "Steam", [(180, 0.6)]),
    ("steamwebhelper.exe", None, [(210, 1.1), (150, 0.7)]),
)


_ALIASES = {"steamwebhelper.exe": "steam.exe"}   # допоміжні процеси закриваються разом із програмою


class World:
    def __init__(self):
        self.procs: list[dict] = []
        self.closing: dict[str, float] = {}   # exe (нижній регістр) -> момент початку звільнення, с
        self.clock = 0.0
        pid = 910_001
        roots: dict[str, int] = {}
        for exe, title, members in _APPS:
            key = exe.lower()
            first = pid
            for i, (mem, cpu) in enumerate(members):
                if key == "steamwebhelper.exe":
                    ppid = roots["steam.exe"]
                elif key == "svchost.exe":
                    ppid = roots["services.exe"]       # оболонка Windows не групує дочірні процеси
                else:
                    ppid = 0 if i == 0 else first
                self.procs.append({"pid": pid, "ppid": ppid, "create_time": CREATE_TIME, "name": exe,
                                   "mem": float(mem), "cpu": float(cpu)})
                pid += 1
            roots.setdefault(key, first)
            if title:
                process_groups._title_cache[(first, CREATE_TIME)] = (title, None)

    # ----------------------------------------------------------------- стан

    def close_app(self, exe: str) -> None:
        """Імітація закриття: пам'ять програми плавно звільняється, потім процеси зникають зі списку."""
        self.closing.setdefault(exe.lower(), self.clock)

    def _factor(self, proc: dict) -> float:
        name = proc["name"].lower()
        started = self.closing.get(_ALIASES.get(name, name))
        if started is None:
            return 1.0
        k = (self.clock - started - RELEASE_DELAY_S) / RELEASE_S
        if k <= 0:
            return 1.0
        k = min(k, 1.0)
        return 1.0 - (k * k * (3 - 2 * k))   # smoothstep

    def processes(self, track_cpu: bool = True) -> list[dict]:
        out = []
        for p in self.procs:
            f = self._factor(p)
            if f <= 0.0:
                continue
            wobble = 1.0 + 0.012 * math.sin(self.clock * 1.7 + p["pid"])
            out.append({"pid": p["pid"], "ppid": p["ppid"], "create_time": p["create_time"], "name": p["name"],
                        "cpu_percent": p["cpu"] * f * wobble, "memory_mb": p["mem"] * f * wobble})
        return out

    def ram_used_gb(self) -> float:
        return OTHER_GB + sum(p["memory_mb"] for p in self.processes()) / 1024

    def snapshot(self) -> dict:
        procs = self.processes()
        used = OTHER_GB + sum(p["memory_mb"] for p in procs) / 1024
        c = self.clock
        cpu = 17.0 + sum(p["cpu_percent"] for p in procs) * 0.9 + 3.2 * math.sin(c * 0.9) + 1.6 * math.sin(c * 2.3)
        gpu = 13.0 + 3.0 * math.sin(c * 0.7) + 1.5 * math.sin(c * 1.9)
        return {
            "cpu_percent": max(3.0, min(cpu, 97.0)),
            "cpu_freq_ghz": 4.05 + 0.08 * math.sin(c * 0.5),
            "ram_percent": used / TOTAL_GB * 100.0,
            "ram_used_gb": used,
            "ram_total_gb": TOTAL_GB,
            "gpu": {"name": GPU_NAME, "load_percent": gpu, "mem_used_mb": 2150.0 + 20 * math.sin(c),
                    "mem_total_mb": 12288.0, "temperature_c": 52.0 + 1.2 * math.sin(c * 0.4)},
            "cpu_temp": 58.0 + 2.0 * math.sin(c * 0.6),
            "cpu_sensors": {"available": True, "temp": 58.0, "core_temps": {}, "core_clocks": {}},
            "temp_threshold": 85,
            "uptime_text": "3 h 12 min",
            "processes": procs,
            "process_groups": process_groups.group_processes(procs),
            "memory_compression_mb": 0.0,
            "disk_read_mb_s": 0.4 + 0.2 * math.sin(c * 1.3) ** 2,
            "disk_write_mb_s": 0.2 + 0.1 * math.sin(c * 0.8) ** 2,
            "net_down_mb_s": 0.35 + 0.15 * math.sin(c * 1.1) ** 2,
            "net_up_mb_s": 0.05 + 0.03 * math.sin(c * 0.9) ** 2,
        }


def install(world: World) -> None:
    """Підміна джерел даних Монітора й Ігрового режиму на вигадані (читання не чіпає ПК)."""
    monitor_core._all_processes = lambda track_cpu=True: world.processes(track_cpu)
    monitor_core.collect_snapshot = lambda include_processes=True: world.snapshot()
    monitor_core.prime = lambda: None
