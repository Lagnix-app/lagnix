"""Завантаження та збереження налаштувань PulseFPS (settings.json) — усе,
що керується з вкладки «Налаштування». Runtime-дані (історія тестів,
бекапи твіків, автозапуск тощо) живуть окремо в core/app_data.py
(data.json), щоб не змішувати те, що редагує користувач, з тим, що
накопичує сама програма. Старий об'єднаний config.json мігрується
автоматично один раз (core/migrate.py)."""

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
    "sounds_enabled": True,
    "sounds_volume": 0.25,
    "sounds_hover_volume": 0.125,
    "monitor_update_interval_s": 1.0,
    "animations_enabled": True,
    "robot_animation_enabled": True,
    # "exit" — закривати програму; "tray" — згортати в трей.
    "close_action": "exit",
}


def load_settings() -> dict:
    migrate_if_needed()

    if not os.path.exists(SETTINGS_PATH):
        save_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)

    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SETTINGS)

    merged = dict(DEFAULT_SETTINGS)
    merged.update(data)
    return merged


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
