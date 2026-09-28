"""Дані для вкладки «Система»: характеристики заліза, розумні підказки та
звіт «що гальмує мій ПК».

Статика (CPU/GPU/RAM/Windows/диски/монітори) збирається з кількох джерел:
- CPU і версія Windows — напряму з реєстру (winreg), без сторонніх залежностей;
- GPU (модель/пам'ять/драйвер), частота RAM і тип дисків (SSD/HDD) — одним
  разовим викликом PowerShell (Get-CimInstance + Storage-командлети),
  результат кешується на весь час роботи процесу, бо ці дані не змінюються
  "на льоту";
- монітори (роздільність, поточна/максимальна частота оновлення) — напряму
  через user32 (EnumDisplayDevices/EnumDisplaySettings), без залежностей;
- живе навантаження (CPU/RAM/GPU/диск) — psutil і (для GPU) nvidia-smi,
  як і в core/monitor.py.

Усюди широкий try/except: якщо якесь джерело недоступне (немає PowerShell,
немає nvidia-smi, невідомий формат тощо) — повертаємо "невідомо"/None замість
падіння, як просив підхід "без падінь".
"""

import ctypes
import json
import os
import re
import shutil
import subprocess
import threading
import time
import winreg
from ctypes import wintypes
from datetime import datetime

import psutil

from core import game_mode as game_mode_core
from core import monitor as monitor_core
from core.logging_setup import get_logger
from core.system_processes import is_hidden

_logger = get_logger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NVIDIA_SMI = shutil.which("nvidia-smi")
_LOGICAL_CPU_COUNT = psutil.cpu_count(logical=True) or 1

UNKNOWN = "невідомо"

REPORT_DURATION_SEC = 60
REPORT_SAMPLE_INTERVAL_SEC = 1.0

_RATING_LABELS = ("Відмінно", "Добре", "Задовільно", "Погано")
_RATING_COLORS = ("#2ee59d", "#d4b106", "#e0a52f", "#ff5c7a")

ADVICE_TAB_LABELS = {
    "game_mode": "Ігровий режим",
    "autostart": "Автозапуск",
    "cleanup": "Очищення",
    "registry_tweaks": "Твіки реєстру",
}

_DRIVER_PAGES = {
    "nvidia": "https://www.nvidia.com/Download/index.aspx",
    "geforce": "https://www.nvidia.com/Download/index.aspx",
    "amd": "https://www.amd.com/en/support",
    "radeon": "https://www.amd.com/en/support",
    "intel": "https://www.intel.com/content/www/us/en/support/detect.html",
}


# ------------------------------------------------------------------ CPU

def _cpu_model_name() -> str:
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        ) as key:
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            return str(name).strip()
    except OSError:
        return UNKNOWN


def get_cpu_info() -> dict:
    freq_ghz = None
    try:
        freq_info = psutil.cpu_freq()
        if freq_info:
            value = freq_info.max or freq_info.current
            if value:
                freq_ghz = round(value / 1000, 2)
    except Exception:
        freq_ghz = None

    return {
        "model": _cpu_model_name(),
        "cores_physical": psutil.cpu_count(logical=False) or None,
        "cores_logical": psutil.cpu_count(logical=True) or None,
        "freq_ghz": freq_ghz,
    }


# --------------------------------------------------------- WMI/PowerShell

_cim_cache_lock = threading.Lock()
_cim_cache: dict | None = None

_CIM_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$gpu = Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion,DriverDate
$ramSpeed = (Get-CimInstance Win32_PhysicalMemory | Select-Object -First 1 -ExpandProperty Speed)
$disks = @()
foreach ($vol in (Get-Volume | Where-Object { $_.DriveLetter })) {
    $media = 'Unknown'
    try {
        $part = Get-Partition -DriveLetter $vol.DriveLetter -ErrorAction Stop
        $phys = Get-Disk -Number $part.DiskNumber -ErrorAction Stop | Get-PhysicalDisk -ErrorAction Stop
        if ($phys) { $media = [string]($phys | Select-Object -First 1 -ExpandProperty MediaType) }
    } catch {}
    $disks += [PSCustomObject]@{ Letter = $vol.DriveLetter; Media = $media }
}
[PSCustomObject]@{ Gpu = @($gpu); RamSpeed = $ramSpeed; Disks = @($disks) } | ConvertTo-Json -Depth 4 -Compress
"""


def _parse_wmi_date(value):
    if not value or not isinstance(value, str):
        return None
    match = re.match(r"/Date\((\d+)\)/", value)
    if match:
        try:
            return datetime.fromtimestamp(int(match.group(1)) / 1000).date()
        except (ValueError, OSError):
            return None
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
    if match:
        try:
            return datetime(int(match[1]), int(match[2]), int(match[3])).date()
        except ValueError:
            return None
    match = re.match(r"(\d{4})(\d{2})(\d{2})\d{6}", value)
    if match:
        try:
            return datetime(int(match[1]), int(match[2]), int(match[3])).date()
        except ValueError:
            return None
    return None


def _run_static_cim_query() -> dict:
    result = {"gpus": [], "ram_speed_mhz": None, "disks": []}
    try:
        raw = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _CIM_SCRIPT],
            stderr=subprocess.STDOUT, timeout=10, creationflags=_NO_WINDOW,
        ).decode("utf-8", errors="ignore").strip()
        data = json.loads(raw) if raw else {}
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError, ValueError):
        _logger.exception("Не вдалося зібрати дані WMI/PowerShell для вкладки «Система»")
        return result

    for entry in data.get("Gpu") or []:
        entry = entry or {}
        name = entry.get("Name")
        if not name:
            continue
        adapter_ram = entry.get("AdapterRAM")
        memory_mb = (
            adapter_ram / (1024 ** 2)
            if isinstance(adapter_ram, (int, float)) and adapter_ram > 0
            else None
        )
        result["gpus"].append({
            "name": name,
            "memory_mb": memory_mb,
            "driver_version": entry.get("DriverVersion"),
            "driver_date": _parse_wmi_date(entry.get("DriverDate")),
        })

    ram_speed = data.get("RamSpeed")
    if isinstance(ram_speed, (int, float)) and ram_speed > 0:
        result["ram_speed_mhz"] = int(ram_speed)

    for entry in data.get("Disks") or []:
        entry = entry or {}
        letter = entry.get("Letter")
        if letter:
            result["disks"].append({
                "letter": str(letter).upper(),
                "media": str(entry.get("Media", "Unknown")),
            })

    return result


def _query_static_cim() -> dict:
    global _cim_cache
    with _cim_cache_lock:
        if _cim_cache is None:
            _cim_cache = _run_static_cim_query()
        return _cim_cache


# ------------------------------------------------------------------ GPU

def _nvidia_static_info() -> dict | None:
    if not _NVIDIA_SMI:
        return None
    try:
        raw = subprocess.check_output(
            [_NVIDIA_SMI, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader,nounits"],
            stderr=subprocess.STDOUT, timeout=3, creationflags=_NO_WINDOW,
        ).decode("utf-8", errors="ignore").strip()
    except (subprocess.SubprocessError, OSError):
        return None

    if not raw:
        return None
    parts = [p.strip() for p in raw.splitlines()[0].split(",")]
    if len(parts) < 3:
        return None
    try:
        return {"name": parts[0], "driver_version": parts[1], "memory_total_mb": float(parts[2])}
    except ValueError:
        return None


def get_gpu_info() -> dict:
    cim = _query_static_cim()
    gpu_entries = [g for g in cim["gpus"] if g["name"] and "basic render" not in g["name"].lower()]

    chosen = None
    if gpu_entries:
        nvidia_entries = [g for g in gpu_entries if "nvidia" in g["name"].lower()]
        chosen = nvidia_entries[0] if nvidia_entries else gpu_entries[0]

    model = chosen["name"] if chosen else UNKNOWN
    driver_version = chosen["driver_version"] if chosen else None
    driver_date = chosen["driver_date"] if chosen else None
    memory_mb = chosen["memory_mb"] if chosen else None

    nv = _nvidia_static_info()
    if nv:
        model = nv["name"]
        driver_version = nv["driver_version"]
        memory_mb = nv["memory_total_mb"]

    return {
        "model": model or UNKNOWN,
        "memory_mb": memory_mb,
        "driver_version": driver_version or UNKNOWN,
        "driver_date": driver_date,
    }


def driver_page_url(gpu_model: str) -> str | None:
    """Посилання на офіційну сторінку драйверів за назвою відеокарти, або None,
    якщо виробника не вдалося розпізнати (тоді UI пропонує Windows Update)."""
    lowered = (gpu_model or "").lower()
    for key, url in _DRIVER_PAGES.items():
        if key in lowered:
            return url
    return None


# ------------------------------------------------------------------ RAM

def get_ram_info() -> dict:
    mem = psutil.virtual_memory()
    cim = _query_static_cim()
    return {
        "total_gb": mem.total / (1024 ** 3),
        "used_gb": mem.used / (1024 ** 3),
        "free_gb": mem.available / (1024 ** 3),
        "percent": mem.percent,
        "speed_mhz": cim.get("ram_speed_mhz"),
    }


# -------------------------------------------------------------- Windows

def _format_uptime(seconds: float) -> str:
    total_minutes = int(seconds // 60)
    days, rem_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem_minutes, 60)
    parts = []
    if days:
        parts.append(f"{days} дн.")
    if hours or days:
        parts.append(f"{hours} год")
    parts.append(f"{minutes} хв")
    return " ".join(parts)


def get_windows_info() -> dict:
    product_name, display_version, build, ubr = "Windows", "", "", ""
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
        ) as key:
            def _val(name, default=""):
                try:
                    return winreg.QueryValueEx(key, name)[0]
                except OSError:
                    return default

            product_name = _val("ProductName", "Windows")
            display_version = _val("DisplayVersion") or _val("ReleaseId", "")
            build = _val("CurrentBuildNumber", "")
            ubr = _val("UBR", "")
    except OSError:
        pass

    version_text = f"{product_name} {display_version}".strip() if product_name else UNKNOWN
    build_text = f"{build}.{ubr}" if build and ubr != "" else (str(build) if build else UNKNOWN)

    uptime_text = UNKNOWN
    uptime_days = None
    try:
        seconds = time.time() - psutil.boot_time()
        uptime_days = int(seconds // 86400)
        uptime_text = _format_uptime(seconds)
    except Exception:
        pass

    return {
        "version": version_text,
        "build": build_text,
        "uptime_text": uptime_text,
        "uptime_days": uptime_days,
    }


# ---------------------------------------------------------------- диски

def get_disks_info() -> list[dict]:
    media_by_letter = {d["letter"]: d["media"] for d in _query_static_cim()["disks"]}

    result = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        letter = part.device.rstrip("\\").rstrip(":").upper()
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        if usage.total == 0:
            continue

        media = media_by_letter.get(letter, "Unknown").lower()
        if media == "ssd":
            disk_type = "SSD"
        elif media == "hdd":
            disk_type = "HDD"
        else:
            disk_type = UNKNOWN

        result.append({
            "letter": f"{letter}:",
            "type": disk_type,
            "total_gb": usage.total / (1024 ** 3),
            "free_gb": usage.free / (1024 ** 3),
            "free_percent": usage.free / usage.total * 100,
        })

    result.sort(key=lambda d: d["letter"])
    return result


# ------------------------------------------------------------- монітори

class _DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmOrientation", ctypes.c_short),
        ("dmPaperSize", ctypes.c_short),
        ("dmPaperLength", ctypes.c_short),
        ("dmPaperWidth", ctypes.c_short),
        ("dmScale", ctypes.c_short),
        ("dmCopies", ctypes.c_short),
        ("dmDefaultSource", ctypes.c_short),
        ("dmPrintQuality", ctypes.c_short),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", ctypes.c_wchar * 32),
        ("DeviceString", ctypes.c_wchar * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", ctypes.c_wchar * 128),
        ("DeviceKey", ctypes.c_wchar * 128),
    ]


_DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
_ENUM_CURRENT_SETTINGS = -1


def _monitor_display_name(user32, adapter_device_name: str, fallback: str) -> str:
    """DeviceString адаптера — це назва відеокарти, не монітора; реальну (хай і
    загальну, на кшталт "Generic PnP Monitor") назву дає вкладений виклик
    EnumDisplayDevicesW із іменем адаптера."""
    monitor = _DISPLAY_DEVICEW()
    monitor.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
    if user32.EnumDisplayDevicesW(adapter_device_name, 0, ctypes.byref(monitor), 0):
        if monitor.DeviceString:
            return monitor.DeviceString
    return fallback


def get_monitors_info() -> list[dict]:
    monitors: list[dict] = []
    try:
        user32 = ctypes.windll.user32
        index = 0
        while True:
            device = _DISPLAY_DEVICEW()
            device.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
            if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
                break
            index += 1
            if not (device.StateFlags & _DISPLAY_DEVICE_ATTACHED_TO_DESKTOP):
                continue

            current = _DEVMODEW()
            current.dmSize = ctypes.sizeof(_DEVMODEW)
            if not user32.EnumDisplaySettingsW(device.DeviceName, _ENUM_CURRENT_SETTINGS, ctypes.byref(current)):
                continue

            width, height, current_hz = current.dmPelsWidth, current.dmPelsHeight, current.dmDisplayFrequency
            max_hz = current_hz

            mode_index = 0
            while True:
                mode = _DEVMODEW()
                mode.dmSize = ctypes.sizeof(_DEVMODEW)
                if not user32.EnumDisplaySettingsW(device.DeviceName, mode_index, ctypes.byref(mode)):
                    break
                if mode.dmPelsWidth == width and mode.dmPelsHeight == height:
                    max_hz = max(max_hz, mode.dmDisplayFrequency)
                mode_index += 1

            name = _monitor_display_name(user32, device.DeviceName, f"Монітор {len(monitors) + 1}")
            monitors.append({
                "name": name,
                "width": width,
                "height": height,
                "current_hz": current_hz,
                "max_hz": max_hz,
            })
    except Exception:
        _logger.exception("Не вдалося отримати дані про монітори")
        return []

    return monitors


# --------------------------------------------------------------- знімок

def collect_static_snapshot() -> dict:
    return {
        "cpu": get_cpu_info(),
        "gpu": get_gpu_info(),
        "ram": get_ram_info(),
        "windows": get_windows_info(),
        "disks": get_disks_info(),
        "monitors": get_monitors_info(),
    }


# --------------------------------------------------------- розумні підказки

def build_smart_tips(snapshot: dict) -> list[dict]:
    tips = []

    for mon in snapshot["monitors"]:
        if mon["current_hz"] and mon["max_hz"] and mon["max_hz"] > mon["current_hz"] + 1:
            tips.append({
                "id": f"monitor_hz_{mon['name']}",
                "text": (
                    f"Монітор «{mon['name']}» зараз працює на {mon['current_hz']} Гц, хоча підтримує "
                    f"до {mon['max_hz']} Гц. Вищу частоту можна увімкнути в параметрах дисплея."
                ),
                "action": "open_display_settings",
            })

    gpu = snapshot["gpu"]
    driver_date = gpu.get("driver_date")
    if driver_date is not None:
        age_days = (datetime.now().date() - driver_date).days
        if age_days > 182:
            tips.append({
                "id": "gpu_driver_old",
                "text": (
                    f"Драйвер відеокарти не оновлювався близько {age_days // 30} міс. "
                    "Новіша версія може покращити продуктивність і стабільність в іграх."
                ),
                "action": "open_driver_page",
                "gpu_model": gpu["model"],
            })

    for disk in snapshot["disks"]:
        if disk["free_percent"] < 10:
            tips.append({
                "id": f"disk_low_{disk['letter']}",
                "text": (
                    f"На диску {disk['letter']} залишилось лише {disk['free_gb']:.0f} ГБ "
                    f"({disk['free_percent']:.0f}%) вільного місця."
                ),
                "action": "open_cleanup_tab",
            })

    uptime_days = snapshot["windows"].get("uptime_days")
    if uptime_days is not None and uptime_days >= 7:
        tips.append({
            "id": "uptime_long",
            "text": (
                f"Комп'ютер не перезавантажувався {uptime_days} дн. "
                "Перезавантаження часто прибирає накопичені гальма й зависання."
            ),
            "action": "open_windows_update",
        })

    power_guid = game_mode_core.get_active_power_scheme()
    saver_guid = game_mode_core.POWER_PLANS.get("Економія енергії", "")
    if power_guid and saver_guid and power_guid.lower() == saver_guid.lower():
        tips.append({
            "id": "power_saver",
            "text": "Увімкнений план живлення «Економія енергії» — він навмисно знижує швидкодію системи.",
            "action": "switch_to_balanced",
        })

    return tips


# ------------------------------------------------------ звіт «що гальмує ПК»

def run_diagnostic(duration_sec: float, stop_event: threading.Event) -> dict:
    """Виконується у фоновому потоці: ~duration_sec секунд збирає CPU/RAM/GPU/диск
    і навантаження процесів, потім повертає підсумковий звіт."""
    try:
        monitor_core.prime()
    except Exception:
        _logger.exception("Не вдалося ініціалізувати збір даних для звіту")

    started = time.monotonic()
    cpu_samples: list[float] = []
    ram_samples: list[float] = []
    gpu_samples: list[float] = []
    disk_samples: list[float] = []
    proc_totals: dict[int, dict] = {}
    current_pid = os.getpid()

    last_io = None
    try:
        last_io = psutil.disk_io_counters()
    except Exception:
        last_io = None
    last_io_time = time.monotonic()

    while not stop_event.is_set():
        elapsed = time.monotonic() - started
        if elapsed >= duration_sec:
            break

        cpu_samples.append(psutil.cpu_percent(interval=None))
        ram_samples.append(psutil.virtual_memory().percent)

        try:
            gpu = monitor_core.get_gpu_info()
        except Exception:
            gpu = None
        if gpu is not None:
            gpu_samples.append(gpu["load_percent"])

        now = time.monotonic()
        try:
            io = psutil.disk_io_counters()
        except Exception:
            io = None
        if io is not None and last_io is not None:
            dt_ms = max((now - last_io_time) * 1000, 1.0)
            busy_ms = (io.read_time - last_io.read_time) + (io.write_time - last_io.write_time)
            disk_samples.append(max(0.0, min(100.0, busy_ms / dt_ms * 100)))
        last_io, last_io_time = io, now

        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                info = proc.info
                pid = info["pid"]
                name = info["name"] or "—"
                if pid == current_pid or is_hidden(name):
                    continue
                entry = proc_totals.setdefault(pid, {"name": name, "cpu": [], "ram": []})
                entry["cpu"].append((info["cpu_percent"] or 0.0) / _LOGICAL_CPU_COUNT)
                entry["ram"].append(info["memory_percent"] or 0.0)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        stop_event.wait(REPORT_SAMPLE_INTERVAL_SEC)

    def _avg(values):
        return sum(values) / len(values) if values else 0.0

    cpu_avg, ram_avg, disk_avg = _avg(cpu_samples), _avg(ram_samples), _avg(disk_samples)
    gpu_avg = _avg(gpu_samples) if gpu_samples else None

    offenders = [
        {"pid": pid, "name": e["name"], "cpu_avg": _avg(e["cpu"]), "ram_avg": _avg(e["ram"])}
        for pid, e in proc_totals.items()
    ]
    offenders.sort(key=lambda p: p["cpu_avg"] + p["ram_avg"], reverse=True)
    top_offenders = offenders[:5]

    score, label, color = _rate_load(cpu_avg, ram_avg, gpu_avg, disk_avg)
    advice = _build_advice(cpu_avg, ram_avg, gpu_avg, disk_avg, top_offenders)

    return {
        "cpu_avg": cpu_avg,
        "ram_avg": ram_avg,
        "gpu_avg": gpu_avg,
        "disk_avg": disk_avg,
        "score": score,
        "label": label,
        "color": color,
        "top_offenders": top_offenders,
        "advice": advice,
    }


def _rate_load(cpu, ram, gpu, disk) -> tuple[int, str, str]:
    values = [cpu, ram, disk]
    if gpu is not None:
        values.append(gpu)
    avg = sum(values) / len(values)
    score = max(0, min(100, round(100 - avg)))

    if score >= 80:
        level = 0
    elif score >= 60:
        level = 1
    elif score >= 40:
        level = 2
    else:
        level = 3
    return score, _RATING_LABELS[level], _RATING_COLORS[level]


def _build_advice(cpu, ram, gpu, disk, top_offenders) -> list[dict]:
    advice = []

    if cpu >= 70:
        advice.append({
            "text": (
                "Процесор був сильно завантажений майже весь час перевірки. Увімкни "
                "Ігровий режим перед грою — він закриває зайві фонові програми."
            ),
            "tab_key": "game_mode",
        })
    if ram >= 80:
        advice.append({
            "text": (
                "Оперативної пам'яті майже не залишається. Перевір автозапуск — можливо, "
                "забагато програм стартує разом із Windows."
            ),
            "tab_key": "autostart",
        })
    if disk >= 55:
        advice.append({
            "text": (
                "Диск був сильно завантажений операціями читання/запису. Очищення "
                "тимчасових файлів і кешу може трохи розвантажити його."
            ),
            "tab_key": "cleanup",
        })
    if gpu is not None and gpu >= 85:
        advice.append({
            "text": (
                "Відеокарта працювала майже на межі. Якщо це сталось не під час гри — "
                "перевір, чи не займає GPU браузер або програма для стрімів."
            ),
            "tab_key": "game_mode",
        })

    if top_offenders:
        leader = top_offenders[0]
        if leader["cpu_avg"] >= 15 or leader["ram_avg"] >= 15:
            advice.append({
                "text": (
                    f"Найбільше ресурсів забирав процес «{leader['name']}» "
                    f"(CPU {leader['cpu_avg']:.0f}%, RAM {leader['ram_avg']:.0f}%)."
                ),
                "tab_key": None,
            })

    if not advice:
        advice.append({
            "text": "Суттєвих проблем під час перевірки не знайдено — система в непоганому стані.",
            "tab_key": None,
        })

    advice.append({
        "text": "Ігрові твіки реєстру можуть додати трохи продуктивності в іграх.",
        "tab_key": "registry_tweaks",
    })

    return advice[:5]


# -------------------------------------------------------------- текст копій

def build_system_info_text(snapshot: dict) -> str:
    cpu, ram, gpu, windows = snapshot["cpu"], snapshot["ram"], snapshot["gpu"], snapshot["windows"]

    core_bits = []
    if cpu.get("cores_physical"):
        core_bits.append(f"{cpu['cores_physical']} ядер")
    if cpu.get("cores_logical"):
        core_bits.append(f"{cpu['cores_logical']} потоків")
    cpu_line = f"Процесор: {cpu['model']}"
    if core_bits:
        cpu_line += f", {' / '.join(core_bits)}"
    if cpu.get("freq_ghz"):
        cpu_line += f", {cpu['freq_ghz']} ГГц"

    gpu_line = f"Відеокарта: {gpu['model']}"
    if gpu.get("memory_mb"):
        gpu_line += f", {gpu['memory_mb'] / 1024:.1f} ГБ"
    gpu_line += f", драйвер {gpu['driver_version']}"
    if gpu.get("driver_date"):
        gpu_line += f" від {gpu['driver_date'].strftime('%d.%m.%Y')}"

    ram_line = f"Оперативна пам'ять: {ram['total_gb']:.1f} ГБ"
    if ram.get("speed_mhz"):
        ram_line += f", {ram['speed_mhz']} МГц"
    ram_line += f", зайнято {ram['used_gb']:.1f} ГБ ({ram['percent']:.0f}%)"

    lines = [
        "=== Інформація про систему (PulseFPS) ===",
        f"Windows: {windows['version']} (збірка {windows['build']}), час роботи: {windows['uptime_text']}",
        cpu_line,
        gpu_line,
        ram_line,
    ]

    for disk in snapshot["disks"]:
        lines.append(
            f"Диск {disk['letter']} ({disk['type']}): {disk['free_gb']:.0f} / {disk['total_gb']:.0f} ГБ "
            f"вільно ({disk['free_percent']:.0f}%)"
        )
    for mon in snapshot["monitors"]:
        lines.append(
            f"Монітор «{mon['name']}»: {mon['width']}x{mon['height']}, {mon['current_hz']} Гц "
            f"(макс. {mon['max_hz']} Гц)"
        )

    return "\n".join(lines)


def build_report_text(report: dict) -> str:
    lines = [
        "=== Звіт: що гальмує ПК (PulseFPS) ===",
        f"Оцінка: {report['label']} ({report['score']}/100)",
        (
            f"CPU: {report['cpu_avg']:.0f}%   RAM: {report['ram_avg']:.0f}%   "
            f"Диск: {report['disk_avg']:.0f}%"
            + (f"   GPU: {report['gpu_avg']:.0f}%" if report["gpu_avg"] is not None else "")
        ),
    ]

    if report["top_offenders"]:
        lines.append("Найбільше навантажували систему:")
        for proc in report["top_offenders"]:
            lines.append(f"  • {proc['name']} — CPU {proc['cpu_avg']:.0f}%, RAM {proc['ram_avg']:.0f}%")

    lines.append("Поради:")
    for item in report["advice"]:
        lines.append(f"  • {item['text']}")

    return "\n".join(lines)
