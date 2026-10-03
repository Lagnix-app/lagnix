"""Звірка метрик PulseFPS із Диспетчером завдань: друкує в консоль сирі й
згладжені значення щосекунди. Запуск: python tools/compare_monitor.py [секунд]"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psutil  # noqa: E402

from core import monitor, perf_counters  # noqa: E402


def main() -> None:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    monitor.prime()
    time.sleep(1.0)
    print(f"Base CPU frequency: {monitor._get_cpu_base_mhz()} MHz")
    print(f"{'t':>3} | {'GPU 3D (PDH)':>12} | {'GPU NVML':>8} | {'GPU shown':>10} | "
          f"{'CPU perf%':>9} | {'CPU GHz (PDH)':>13} | {'CPU GHz (psutil)':>16} | {'CPU GHz shown':>14}")
    for i in range(seconds):
        perf = perf_counters.sample()
        nvml = monitor._gpu_info_nvml()
        snap_gpu = monitor.get_gpu_info(perf["gpu_load"])
        shown_gpu = monitor._gpu_load_avg.add(snap_gpu["load_percent"]) if snap_gpu else None
        freq = monitor.get_cpu_freq_ghz(perf["cpu_perf_percent"])
        shown_freq = monitor._cpu_freq_avg.add(freq)
        psu = psutil.cpu_freq()

        def f(v, fmt="{:.1f}"):
            return "—" if v is None else fmt.format(v)

        print(f"{i:>3} | {f(perf['gpu_load']):>12} | {f(nvml['load_percent'] if nvml else None, '{:.0f}'):>8} | "
              f"{f(shown_gpu):>10} | {f(perf['cpu_perf_percent']):>9} | {f(freq, '{:.2f}'):>13} | "
              f"{f(psu.current / 1000 if psu else None, '{:.2f}'):>16} | {f(shown_freq, '{:.2f}'):>14}")
        time.sleep(1.0)


if __name__ == "__main__":
    main()
