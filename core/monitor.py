"""Збір системних метрик (CPU, RAM, GPU, температури, процеси) для вкладки «Монітор»."""

import os
import shutil
import subprocess

import psutil

from core.settings import load_settings
from core.system_processes import is_hidden

try:
    import wmi as _wmi
except ImportError:
    _wmi = None

DEFAULT_TEMP_THRESHOLD_C = 85

_NVIDIA_SMI = shutil.which("nvidia-smi")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_LOGICAL_CPU_COUNT = psutil.cpu_count(logical=True) or 1


def prime() -> None:
    """Ініціалізує лічильники psutil, щоб перше реальне вимірювання було коректним."""
    psutil.cpu_percent(interval=None)
    for proc in psutil.process_iter(["cpu_percent"]):
        pass


def get_cpu_ram_usage() -> dict:
    mem = psutil.virtual_memory()
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_percent": mem.percent,
        "ram_used_gb": mem.used / (1024 ** 3),
        "ram_total_gb": mem.total / (1024 ** 3),
    }


def get_gpu_info() -> dict | None:
    """Дані NVIDIA GPU через nvidia-smi, або None, якщо недоступно."""
    if not _NVIDIA_SMI:
        return None

    try:
        raw = subprocess.check_output(
            [
                _NVIDIA_SMI,
                "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            stderr=subprocess.STDOUT,
            timeout=2,
            creationflags=_NO_WINDOW,
        ).decode("utf-8", errors="ignore").strip()
    except (subprocess.SubprocessError, OSError):
        return None

    if not raw:
        return None

    parts = [p.strip() for p in raw.splitlines()[0].split(",")]
    if len(parts) < 4:
        return None

    try:
        return {
            "load_percent": float(parts[0]),
            "mem_used_mb": float(parts[1]),
            "mem_total_mb": float(parts[2]),
            "temperature_c": float(parts[3]),
        }
    except ValueError:
        return None


def get_cpu_temperature() -> float | None:
    """Температура CPU, якщо її вдається отримати будь-яким доступним способом."""
    try:
        temps = psutil.sensors_temperatures()
    except (AttributeError, NotImplementedError, OSError):
        temps = {}

    for label in ("coretemp", "k10temp", "cpu_thermal", "cpu-thermal"):
        entries = temps.get(label)
        if entries:
            return entries[0].current

    for entries in temps.values():
        if entries:
            return entries[0].current

    if _wmi is not None:
        try:
            conn = _wmi.WMI(namespace="root\\WMI")
            zones = conn.MSAcpi_ThermalZoneTemperature()
            if zones:
                return (zones[0].CurrentTemperature / 10.0) - 273.15
        except Exception:
            pass

    return None


def get_top_processes(limit: int = 10):
    """Повертає (топ за CPU, топ за RAM) — списки словників pid/name/cpu_percent/memory_percent.

    Виключає процеси ядра ОС (System, System Idle Process) та власний процес PulseFPS.
    Відсоток CPU нормалізується на кількість логічних ядер, щоб максимум був 100%.
    """
    current_pid = os.getpid()
    procs = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = proc.info
            pid = info["pid"]
            name = info["name"] or "—"
            if pid == current_pid or is_hidden(name):
                continue

            procs.append({
                "pid": pid,
                "name": name,
                "cpu_percent": (info["cpu_percent"] or 0.0) / _LOGICAL_CPU_COUNT,
                "memory_percent": info["memory_percent"] or 0.0,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    top_cpu = sorted(procs, key=lambda p: p["cpu_percent"], reverse=True)[:limit]
    top_ram = sorted(procs, key=lambda p: p["memory_percent"], reverse=True)[:limit]
    return top_cpu, top_ram


def terminate_process(pid: int) -> tuple[bool, str]:
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except psutil.TimeoutExpired:
            proc.kill()
        return True, ""
    except psutil.NoSuchProcess:
        return True, ""
    except psutil.AccessDenied:
        return False, "Немає прав для завершення цього процесу"
    except Exception as exc:
        return False, str(exc)


def collect_snapshot() -> dict:
    """Один зріз усіх метрик для вкладки «Монітор»."""
    settings = load_settings()
    threshold = settings.get("temp_threshold_c", DEFAULT_TEMP_THRESHOLD_C)

    usage = get_cpu_ram_usage()
    top_cpu, top_ram = get_top_processes()

    return {
        "cpu_percent": usage["cpu_percent"],
        "ram_percent": usage["ram_percent"],
        "ram_used_gb": usage["ram_used_gb"],
        "ram_total_gb": usage["ram_total_gb"],
        "gpu": get_gpu_info(),
        "cpu_temp": get_cpu_temperature(),
        "temp_threshold": threshold,
        "top_cpu": top_cpu,
        "top_ram": top_ram,
    }
