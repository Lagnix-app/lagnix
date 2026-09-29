"""Завантаження та збереження налаштувань PulseFPS (settings.json) — усе,
що керується з вкладки «Налаштування». Runtime-дані (історія тестів,
бекапи твіків, автозапуск тощо) живуть окремо в core/app_data.py
(data.json), щоб не змішувати те, що редагує користувач, з тим, що
накопичує сама програма. Старий об'єднаний config.json мігрується
автоматично один раз (core/migrate.py)."""

import copy
import json
import os

from core.migrate import migrate_if_needed

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")

DEFAULT_SETTINGS = {
    "theme": "dark",
    "language": "uk",
    "window": {
        "width": 1100,
        "height": 700
    },
    # "last" — відкривати вкладку, на якій програму закрили минулого разу;
    # "monitor" — завжди починати з «Монітора».
    "startup_tab_mode": "last",
    "temp_threshold_c": 85,
    # Розширені датчики (температура CPU) через LibreHardwareMonitorLib.
    # Якщо вимкнено — драйвер датчиків не завантажується.
    "advanced_sensors_enabled": True,
    "sounds_enabled": True,
    "sounds_volume": 0.25,
    "sounds_hover_volume": 0.125,
    "monitor_update_interval_s": 1.0,
    "animations_enabled": True,
    "robot_animation_enabled": True,
    # "exit" — закривати програму; "tray" — згортати в трей.
    "close_action": "exit",
    # Чи згорнуті пояснення (пінг/джитер/втрати) на вкладці «Мережа».
    "network_help_collapsed": False,
    # «Монітор»: процеси згруповані за програмами (як у Диспетчері завдань).
    "monitor_group_processes": True,
}


# Кеш розібраного settings.json за (mtime, розмір): load_settings() кличуть
# часто (Монітор — щосекунди з фонового потоку), а файл змінюється рідко.
_cache_key = None
_cache_data: dict | None = None


def load_settings() -> dict:
    global _cache_key, _cache_data
    migrate_if_needed()

    try:
        st = os.stat(SETTINGS_PATH)
    except OSError:
        save_settings(DEFAULT_SETTINGS)
        return copy.deepcopy(DEFAULT_SETTINGS)

    key = (st.st_mtime_ns, st.st_size)
    if key != _cache_key or _cache_data is None:
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return copy.deepcopy(DEFAULT_SETTINGS)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)
        _cache_key, _cache_data = key, merged
    return copy.deepcopy(_cache_data)  # копія: виклики можуть змінювати словник


def save_settings(settings: dict) -> None:
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=4)


def update_setting(key: str, value) -> dict:
    settings = load_settings()
    settings[key] = value
    save_settings(settings)
    return settings


def reset_to_defaults() -> dict:
    """Скидає лише settings.json (загальні налаштування) — не чіпає
    data.json (історія тестів, збережені початкові значення твіків,
    вимкнені записи автозапуску тощо)."""
    save_settings(DEFAULT_SETTINGS)
    return dict(DEFAULT_SETTINGS)
