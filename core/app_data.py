"""Runtime-дані Lagnix (data.json) — те, що програма сама накопичує й
відновлює (історія тестів мережі, збережені початкові значення твіків,
вимкнені записи автозапуску, стан ігрового режиму), на відміну від
core/settings.py (settings.json), яким керує користувач з вкладки
«Налаштування». Той самий API (load/save/update), щоб модулі, які раніше
працювали з config.json, майже не змінювались."""

import copy
import json
import os
import threading
import time

from core.logging_setup import get_logger
from core.migrate import migrate_if_needed

_log = get_logger("core.app_data")
# Читання-зміна-запис кількох потоків (твіки, сесії ігор, вкладки) не повинні губити одне одного.
_lock = threading.RLock()
DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data.json")

DEFAULT_DATA = {
    "last_tab": "monitor",
    "network_test_history": [],
    "autostart_disabled": {},
    "registry_tweaks_initial_state": {},
    "registry_tweaks_backup_done": False,
    "registry_tweaks_backed_up_keys": [],
    "game_mode": {},
}


def _quarantine_corrupt() -> None:
    """Пошкоджений data.json не перезаписуємо мовчки (там початкові значення твіків для
    «повернути як було») — відкладаємо копію поруч."""
    try:
        os.replace(DATA_PATH, DATA_PATH + ".corrupt")
        _log.error("data.json is corrupt; moved to data.json.corrupt, starting with defaults")
    except OSError:
        _log.exception("Could not set the corrupt data.json aside")


def load_data() -> dict:
    migrate_if_needed()

    with _lock:
        if not os.path.exists(DATA_PATH):
            save_data(DEFAULT_DATA)
            return copy.deepcopy(DEFAULT_DATA)

        try:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("data.json root is not an object")
        except (ValueError, OSError) as exc:
            _log.error("Could not read data.json: %s", exc)
            if isinstance(exc, ValueError):
                _quarantine_corrupt()
            return copy.deepcopy(DEFAULT_DATA)

        merged = copy.deepcopy(DEFAULT_DATA)  # глибока копія: виклики змінюють списки/словники
        merged.update(data)
        return merged


def save_data(data: dict) -> None:
    """Атомарний запис (тимчасовий файл + os.replace): збій посеред запису не губить файл."""
    with _lock:
        tmp_path = DATA_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        for attempt in range(5):  # антивірус/індексатор може на мить тримати файл
            try:
                os.replace(tmp_path, DATA_PATH)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)


def update_data(key: str, value) -> dict:
    with _lock:
        data = load_data()
        data[key] = value
        save_data(data)
        return data
