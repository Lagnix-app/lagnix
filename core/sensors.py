"""Розширені датчики (температура CPU тощо) через LibreHardwareMonitorLib (MPL-2.0).

Бібліотека завантажується з libs/ через pythonnet. Версія 0.9.6 НЕ використовує
драйвер WinRing0 — для доступу до MSR/SMU вона користується драйвером PawnIO
(підписаний, його потрібно встановити окремо; без нього температура CPU буде
«Недоступно»). Ініціалізація — один раз, з фонового потоку зі свого циклу опитування;
драйвер не завантажується, якщо датчики вимкнені в налаштуваннях."""

import os
from core import paths as _paths
import sys
import threading
import time

from core.logging_setup import get_logger

_log = get_logger("lagnix.sensors")
_LIBS_DIR = os.path.join(
    _paths.RESOURCE_DIR, "libs"
)

_lock = threading.Lock()
_computer = None
_hw_type = None
_sensor_type = None
_thread: threading.Thread | None = None
_stop = threading.Event()
_interval = 1.0
_data: dict = {"available": False, "reason": "off"}
_failed = False


def _load():
    """Завантажує dll і відкриває Computer. Повертає False при невдачі."""
    global _computer, _hw_type, _sensor_type, _failed
    try:
        import clr  # pythonnet

        dll = os.path.join(_LIBS_DIR, "LibreHardwareMonitorLib.dll")
        if not os.path.exists(dll):
            raise FileNotFoundError(dll)
        if _LIBS_DIR not in sys.path:
            sys.path.append(_LIBS_DIR)
        clr.AddReference(dll)
        from LibreHardwareMonitor.Hardware import Computer, HardwareType, SensorType

        comp = Computer()
        comp.IsCpuEnabled = True
        comp.Open()
        _computer, _hw_type, _sensor_type = comp, HardwareType, SensorType
        return True
    except Exception:
        _failed = True
        _log.exception("Failed to load LibreHardwareMonitorLib")
        return False


def _read() -> dict:
    cpu = None
    for hw in _computer.Hardware:
        if hw.HardwareType == _hw_type.Cpu:
            cpu = hw
            break
    if cpu is None:
        return {"available": False, "reason": "nosensor"}
    cpu.Update()
    core_temps, freqs, fans = {}, {}, []
    package = tctl = hottest = core_avg = power = None
    for s in cpu.Sensors:
        v = s.Value
        if v is None:
            continue
        v = float(v)
        name = str(s.Name)
        st = s.SensorType
        if st == _sensor_type.Temperature:
            low = name.lower()
            if "package" in low:
                package = v
            elif "tctl" in low or "tdie" in low:
                if "tdie" in low or tctl is None:
                    tctl = v
            elif "average" in low:
                core_avg = v
            elif "max" in low:
                hottest = v
            elif "core" in low or "ccd" in low:
                core_temps[name] = v
        elif st == _sensor_type.Clock and "core" in name.lower() and "bus" not in name.lower():
            freqs[name] = v
        elif st == _sensor_type.Power and ("package" in name.lower() or name == "CPU Package"):
            power = v
    temp = package if package is not None else tctl
    if temp is None:
        temp = hottest if hottest is not None else core_avg
    if temp is None and core_temps:
        temp = max(core_temps.values())
    if temp is None:
        return {"available": False, "reason": "nosensor"}
    return {
        "available": True,
        "temp": temp,
        "core_temps": core_temps,
        "core_clocks": freqs,
        "power_w": power,
        "fans": fans,
    }


def _fans() -> list:
    out = []
    try:
        for hw in _computer.Hardware:
            for sub in [hw, *hw.SubHardware]:
                sub.Update()
                for s in sub.Sensors:
                    if s.SensorType == _sensor_type.Fan and s.Value is not None:
                        out.append((str(s.Name), float(s.Value)))
    except Exception:
        pass
    return out


def _current_interval() -> float:
    try:
        from core.settings import load_settings
        return max(0.5, float(load_settings().get("monitor_update_interval_s", 1.0)))
    except Exception:
        return _interval


def _worker():
    global _data
    if not _load():
        _data = {"available": False, "reason": "load_failed"}
        return
    try:
        _computer.IsMotherboardEnabled = True
    except Exception:
        pass
    logged = False
    while not _stop.wait(_current_interval()):
        try:
            d = _read()
            if d["available"]:
                d["fans"] = _fans()
            elif not logged:
                _log.error("CPU temperature sensor not found (PawnIO driver is not installed or the CPU is not supported)")
                logged = True
            _data = d
        except Exception:
            _log.exception("Sensor read error")
            _data = {"available": False, "reason": "read_failed"}
            time.sleep(2)
    try:
        _computer.Close()
    except Exception:
        pass


def start(interval_s: float = 1.0) -> None:
    """Запускає фоновий опитувач (один раз). Нічого не робить, якщо вже запущено."""
    global _thread, _interval, _data
    with _lock:
        _interval = max(0.5, float(interval_s))
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _data = {"available": False, "reason": "starting"}
        _thread = threading.Thread(target=_worker, name="sensors", daemon=True)
        _thread.start()


def stop() -> None:
    """Зупиняє опитування й закриває драйвер."""
    global _thread, _data
    with _lock:
        _stop.set()
        t, _thread = _thread, None
    if t is not None:
        t.join(timeout=3)
    _data = {"available": False, "reason": "off"}


def set_enabled(enabled: bool, interval_s: float = 1.0) -> None:
    if enabled:
        start(interval_s)
    else:
        stop()


def get() -> dict:
    return _data
