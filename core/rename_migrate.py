"""Одноразова міграція після перейменування PulseFPS -> Lagnix: теки «вимкнених»
записів автозапуску, назва плану живлення. Усе ідемпотентне й безпечне при
повторному запуску. Завдання Планувальника мігрує core/launch_on_windows.py."""

import os
import shutil

from core import autostart, power_plans
from core.app_data import load_data
from core.logging_setup import get_logger

_log = get_logger("core.rename_migrate")

_OLD_DISABLED_DIR = "PulseFPS_Disabled"


def _migrate_disabled_dirs() -> None:
    for directory in (autostart._user_startup_dir(), autostart._common_startup_dir()):
        old = os.path.join(directory, _OLD_DISABLED_DIR)
        new = os.path.join(directory, autostart._DISABLED_DIR_NAME)
        if not os.path.isdir(old):
            continue
        try:
            if not os.path.exists(new):
                os.rename(old, new)
            else:
                for name in os.listdir(old):
                    target = os.path.join(new, name)
                    if not os.path.exists(target):
                        shutil.move(os.path.join(old, name), target)
                # порожню стару теку не видаляємо (правило безпеки: видалення лише в whitelist)
            _log.info("Disabled-startup folder migrated: %s", directory)
        except OSError:
            _log.exception("Failed to migrate the disabled-startup folder in %s", directory)


def run() -> None:
    _migrate_disabled_dirs()
    try:
        guid = (load_data().get("game_mode") or {}).get("ultra_guid")
        power_plans.migrate_ultra_name(guid)
    except Exception:
        _log.exception("Failed to rename the power plan")
