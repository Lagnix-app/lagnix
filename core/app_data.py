"""Runtime-дані Lagnix (data.json) — те, що програма сама накопичує й
відновлює (історія тестів мережі, збережені початкові значення твіків,
вимкнені записи автозапуску, стан ігрового режиму), на відміну від
core/settings.py (settings.json), яким керує користувач з вкладки
«Налаштування». Той самий API (load/save/update), щоб модулі, які раніше
працювали з config.json, майже не змінювались."""

import json
import os

from core.migrate import migrate_if_needed

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


def load_data() -> dict:
    migrate_if_needed()

    if not os.path.exists(DATA_PATH):
        save_data(DEFAULT_DATA)
        return dict(DEFAULT_DATA)

    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_DATA)

    merged = dict(DEFAULT_DATA)
    merged.update(data)
    return merged


def save_data(data: dict) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def update_data(key: str, value) -> dict:
    data = load_data()
    data[key] = value
    save_data(data)
    return data
