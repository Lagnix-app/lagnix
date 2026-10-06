"""Одноразова міграція після перейменування PulseFPS -> Lagnix і зі старого способу
вимкнення автозапуску (0.9.4): теки «вимкнених» записів, назва плану живлення. Усе ідемпотентне й безпечне при
повторному запуску. Завдання Планувальника мігрує core/launch_on_windows.py."""

from core import autostart, power_plans
from core.app_data import load_data
from core.logging_setup import get_logger

_log = get_logger("core.rename_migrate")


def run() -> None:
    try:
        autostart.migrate_legacy()
    except Exception:
        _log.exception("Failed to migrate legacy disabled startup entries")
    try:
        guid = (load_data().get("game_mode") or {}).get("ultra_guid")
        power_plans.migrate_ultra_name(guid)
    except Exception:
        _log.exception("Failed to rename the power plan")
