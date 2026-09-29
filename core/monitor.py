"""Збір системних метрик (CPU, RAM, GPU, температури, диск, мережа, процеси)
для вкладки «Монітор»."""

import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque

import psutil

from core import perf_counters, process_groups, process_snapshot, sensors
from core.settings import load_settings
from core.system_processes import is_hidden

try:
    import wmi as _wmi
except ImportError:
    _wmi = None

try:
    import pynvml as _pynvml
except ImportError:
    _pynvml = None

SMOOTHING_WINDOW_S = 3.0  # ковзне середнє для GPU/частоти CPU — щоб графік не скакав

DEFAULT_TEMP_THRESHOLD_C = 85

_NVIDIA_SMI = shutil.which("nvidia-smi")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_LOGICAL_CPU_COUNT = psutil.cpu_count(logical=True) or 1

# Попередні лічильники диска/мережі — для обчислення швидкості (байт/с) як
# різниці між двома знімками; заповнюються в prime() і оновлюються на кожному
# collect_snapshot(). Без цього стану можна лише знати сумарний трафік з
# моменту завантаження ОС, а не поточну швидкість.
_prev_disk_io = None
_prev_net_io = None
_prev_io_time = None


class _MovingAverage:
    """Ковзне середнє за останні `window` секунд (за часом, а не за к-стю
    точок — інтервал опитування налаштовується)."""

    def __init__(self, window: float = SMOOTHING_WINDOW_S):
        self._window = window
        self._samples: deque = deque()

    def add(self, value: float | None) -> float | None:
        now = time.monotonic()
        if value is None:
            self._samples.clear()
            return None
        self._samples.append((now, value))
        while len(self._samples) > 1 and now - self._samples[0][0] > self._window:
            self._samples.popleft()
        return sum(v for _t, v in self._samples) / len(self._samples)


_gpu_load_avg = _MovingAverage()
_cpu_freq_avg = _MovingAverage()

# NVML ініціалізується один раз; nvidia-smi щосекунди — це окремий процес, що
# сам навантажує GPU й дає завищений показник.
_nvml_lock = threading.Lock()
_nvml_handle = None
_nvml_failed = False
_cpu_base_mhz: float | None = None


def _get_nvml_handle():
    global _nvml_handle, _nvml_failed
    if _pynvml is None or _nvml_failed:
        return None
    with _nvml_lock:
        if _nvml_handle is None and not _nvml_failed:
            try:
                _pynvml.nvmlInit()
                _nvml_handle = _pynvml.nvmlDeviceGetHandleByIndex(0)
            except Exception:
                _nvml_failed = True
                return None
    return _nvml_handle


def prime() -> None:
    """Ініціалізує лічильники psutil, PDH і NVML, щоб перше реальне вимірювання було коректним."""
    global _prev_disk_io, _prev_net_io, _prev_io_time
    psutil.cpu_percent(interval=None)
    if process_snapshot.is_available():
        process_snapshot.sample()
    else:
        for _proc in psutil.process_iter(["cpu_percent"]):
            pass
    _prev_disk_io = psutil.disk_io_counters()
    _prev_net_io = psutil.net_io_counters()
    _prev_io_time = time.perf_counter()
    try:
        perf_counters.prime()
    except Exception:
        pass
    _get_nvml_handle()


def _get_cpu_base_mhz() -> float | None:
    """Базова частота CPU (МГц): реєстр Windows, інакше psutil."""
    global _cpu_base_mhz
    if _cpu_base_mhz is None:
        if sys.platform == "win32":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                    _cpu_base_mhz = float(winreg.QueryValueEx(key, "~MHz")[0])
            except OSError:
                pass
        if not _cpu_base_mhz:
            try:
                freq = psutil.cpu_freq()
                _cpu_base_mhz = float(freq.max or freq.current) if freq else None
            except (AttributeError, NotImplementedError, OSError):
                _cpu_base_mhz = None
    return _cpu_base_mhz


def get_cpu_freq_ghz(perf_percent: float | None = None) -> float | None:
    """Поточна частота CPU (ГГц) як у Диспетчері завдань: базова частота ×
    «% Processor Performance». Без лічильника — те, що каже psutil."""
    base = _get_cpu_base_mhz()
    if perf_percent is not None and base:
        return base * perf_percent / 100.0 / 1000.0
    try:
        cpu_freq = psutil.cpu_freq()
        if cpu_freq is not None and cpu_freq.current:
            return cpu_freq.current / 1000.0
    except (AttributeError, NotImplementedError, OSError):
        pass
    return None


def get_cpu_ram_usage(cpu_perf_percent: float | None = None) -> dict:
    mem = psutil.virtual_memory()
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_freq_ghz": get_cpu_freq_ghz(cpu_perf_percent),
        "ram_percent": mem.percent,
        "ram_used_gb": mem.used / (1024 ** 3),
        "ram_total_gb": mem.total / (1024 ** 3),
    }


def _gpu_info_nvml() -> dict | None:
    handle = _get_nvml_handle()
    if handle is None:
        return None
    try:
        name = _pynvml.nvmlDeviceGetName(handle)
        mem = _pynvml.nvmlDeviceGetMemoryInfo(handle)
        return {
            "name": name.decode() if isinstance(name, bytes) else str(name),
            "load_percent": float(_pynvml.nvmlDeviceGetUtilizationRates(handle).gpu),
            "mem_used_mb": mem.used / (1024 ** 2),
            "mem_total_mb": mem.total / (1024 ** 2),
            "temperature_c": float(_pynvml.nvmlDeviceGetTemperature(handle, _pynvml.NVML_TEMPERATURE_GPU)),
        }
    except Exception:
        return None


def _gpu_info_smi() -> dict | None:
    """Запасний варіант без NVML: nvidia-smi (окремий процес, лише NVIDIA)."""
    if not _NVIDIA_SMI:
        return None

    try:
        raw = subprocess.check_output(
            [
                _NVIDIA_SMI,
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
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
    if len(parts) < 5:
        return None

    try:
        return {
            "name": parts[0],
            "load_percent": float(parts[1]),
            "mem_used_mb": float(parts[2]),
            "mem_total_mb": float(parts[3]),
            "temperature_c": float(parts[4]),
        }
    except ValueError:
        return None


def _windows_gpu_name() -> str:
    """Назва відеоадаптера з реєстру (для не-NVIDIA, де немає NVML)."""
    try:
        import winreg
        base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            for i in range(10):
                try:
                    with winreg.OpenKey(root, f"{i:04d}") as sub:
                        return str(winreg.QueryValueEx(sub, "DriverDesc")[0])
                except OSError:
                    continue
    except OSError:
        pass
    return "GPU"


def get_gpu_info(pdh_load: float | None = None) -> dict | None:
    """Дані GPU або None, якщо недоступно.

    Завантаження — з лічильників Windows «GPU Engine» (як у Диспетчері
    завдань, працює і для AMD/Intel; передається як pdh_load), запасні:
    NVML, потім nvidia-smi. Пам'ять і температура — NVML/nvidia-smi.
    Без NVIDIA-джерел, але з лічильником — GPU без VRAM і температури.
    """
    info = _gpu_info_nvml() or _gpu_info_smi()
    if info is None:
        if pdh_load is None:
            return None
        return {
            "name": _windows_gpu_name(), "load_percent": pdh_load,
            "mem_used_mb": 0.0, "mem_total_mb": 0.0, "temperature_c": None,
        }
    if pdh_load is not None:
        info["load_percent"] = pdh_load
    return info


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


def get_io_rates() -> dict:
    """Швидкість диска й мережі (МБ/с) з моменту попереднього виклику."""
    global _prev_disk_io, _prev_net_io, _prev_io_time

    now = time.perf_counter()
    disk = psutil.disk_io_counters()
    net = psutil.net_io_counters()

    dt = now - _prev_io_time if _prev_io_time else None
    if not dt or dt <= 0 or _prev_disk_io is None or _prev_net_io is None:
        rates = {"disk_read_mb_s": 0.0, "disk_write_mb_s": 0.0, "net_down_mb_s": 0.0, "net_up_mb_s": 0.0}
    else:
        mb = 1024 ** 2
        rates = {
            "disk_read_mb_s": max(0.0, (disk.read_bytes - _prev_disk_io.read_bytes) / dt / mb),
            "disk_write_mb_s": max(0.0, (disk.write_bytes - _prev_disk_io.write_bytes) / dt / mb),
            "net_down_mb_s": max(0.0, (net.bytes_recv - _prev_net_io.bytes_recv) / dt / mb),
            "net_up_mb_s": max(0.0, (net.bytes_sent - _prev_net_io.bytes_sent) / dt / mb),
        }

    _prev_disk_io = disk
    _prev_net_io = net
    _prev_io_time = now
    return rates


def get_uptime_text() -> str:
    seconds = max(0.0, time.time() - psutil.boot_time())
    total_minutes = int(seconds // 60)
    days, rem_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem_minutes, 60)

    if days > 0:
        return f"{days} дн {hours} год"
    if hours > 0:
        return f"{hours} год {minutes} хв"
    return f"{minutes} хв"


def get_process_overview() -> tuple[list[dict], list[dict], float | None]:
    """(усі процеси, ті самі процеси, згруповані в програми, розмір «Memory
    Compression» у МБ або None). Список у UI віртуалізований, тож показуємо
    всі процеси, а не лише топ."""
    procs = _all_processes()
    compression = next((p["memory_mb"] for p in procs if p["name"].lower() == "memory compression"), None)
    return procs, process_groups.group_processes(procs), compression


def get_process_groups() -> list[dict]:
    """Групи процесів (як у Моніторі) для інших вкладок: не чіпає стан обчислення
    CPU %, тож можна викликати з довільного потоку, не збиваючи Монітор."""
    return process_groups.group_processes(_all_processes(track_cpu=False))


def _all_processes(track_cpu: bool = True) -> list[dict]:
    """Усі процеси (крім ядра ОС і самого PulseFPS): pid, ppid, create_time,
    name, cpu_percent (нормований на всі ядра, максимум 100%), memory_mb."""
    current_pid = os.getpid()
    if process_snapshot.is_available():
        try:
            procs = [
                p for p in process_snapshot.sample(track_cpu)
                if p["pid"] != current_pid and not is_hidden(p["name"])
            ]
        except OSError:
            procs = _psutil_processes(current_pid)
    else:
        procs = _psutil_processes(current_pid)
    return procs


def _psutil_processes(current_pid: int) -> list[dict]:
    """Запасний шлях (не Windows або збій NtQuerySystemInformation)."""
    procs = []
    # memory_percent свідомо не запитуємо: psutil рахує його через той самий
    # memory_info() — ще один системний виклик на кожен процес. Для сортування
    # "за RAM" досить rss.
    for proc in psutil.process_iter(["pid", "ppid", "name", "cpu_percent", "memory_info"]):
        try:
            info = proc.info
            pid = info["pid"]
            name = info["name"] or "—"
            if pid == current_pid or is_hidden(name):
                continue

            mem_info = info["memory_info"]
            procs.append({
                "pid": pid,
                "ppid": info["ppid"] or 0,
                "create_time": None,
                "name": name,
                "cpu_percent": (info["cpu_percent"] or 0.0) / _LOGICAL_CPU_COUNT,
                "memory_mb": (mem_info.rss / (1024 ** 2)) if mem_info else 0.0,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return procs


def _filetime_to_unix(filetime: int) -> float:
    return filetime / 1e7 - 11644473600.0


def terminate_processes(targets: list[tuple[int, int | None]]) -> tuple[int, list[str]]:
    """Завершує кілька процесів (групу програми): targets — (pid, create_time
    у FILETIME або None). Процес із тим самим PID, але іншим часом створення
    (PID уже перевикористано) не чіпаємо. Спершу м'яке terminate для всіх,
    потім kill для тих, хто не завершився за 3 с. -> (завершено, помилки)."""
    procs, errors = [], []
    for pid, create_time in targets:
        try:
            proc = psutil.Process(pid)
            if create_time and abs(proc.create_time() - _filetime_to_unix(create_time)) > 1.0:
                continue
            proc.terminate()
            procs.append(proc)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            errors.append(f"PID {pid}: немає прав для завершення")
        except Exception as exc:
            errors.append(f"PID {pid}: {exc}")
    gone, alive = psutil.wait_procs(procs, timeout=3)
    killed = len(gone)
    for proc in alive:
        try:
            proc.kill()
            killed += 1
        except psutil.NoSuchProcess:
            killed += 1
        except Exception as exc:
            errors.append(f"PID {proc.pid}: {exc}")
    return killed, errors


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


def collect_snapshot(include_processes: bool = True) -> dict:
    """Один зріз усіх метрик для вкладки «Монітор».

    include_processes=False — коли вкладку не видно: обхід усіх процесів
    (process_iter) найдорожча частина зрізу, а графік навантаження, який
    має накопичувати історію й у фоні, процеси не потребує."""
    settings = load_settings()
    threshold = settings.get("temp_threshold_c", DEFAULT_TEMP_THRESHOLD_C)

    try:
        perf = perf_counters.sample()
    except Exception:
        perf = {"gpu_load": None, "cpu_perf_percent": None}

    usage = get_cpu_ram_usage(perf["cpu_perf_percent"])
    io_rates = get_io_rates()

    gpu = get_gpu_info(perf["gpu_load"])
    if gpu is not None:
        gpu["load_percent"] = _gpu_load_avg.add(gpu["load_percent"])
    else:
        _gpu_load_avg.add(None)
    cpu_sensors = sensors.get()
    freq = _cpu_freq_avg.add(usage["cpu_freq_ghz"])
    processes, groups, compression_mb = get_process_overview() if include_processes else (None, None, None)

    return {
        "cpu_percent": usage["cpu_percent"],
        "cpu_freq_ghz": freq,
        "ram_percent": usage["ram_percent"],
        "ram_used_gb": usage["ram_used_gb"],
        "ram_total_gb": usage["ram_total_gb"],
        "gpu": gpu,
        "cpu_temp": cpu_sensors["temp"] if cpu_sensors.get("available") else None,
        "cpu_sensors": cpu_sensors,
        "temp_threshold": threshold,
        "uptime_text": get_uptime_text(),
        "processes": processes,
        "process_groups": groups,
        "memory_compression_mb": compression_mb,
        **io_rates,
    }
