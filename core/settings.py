"""Завантаження та збереження налаштувань PulseFPS у config.json."""

import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

DEFAULT_SETTINGS = {
    "theme": "dark",
    "language": "uk",
    "window": {
        "width": 1100,
        "height": 700
    },
    "last_tab": "monitor",
    "temp_threshold_c": 85,
    "sounds_enabled": True,
    "sounds_volume": 0.25,
    "sounds_hover_volume": 0.125
}


def load_settings() -> dict:
    if not os.path.exists(CONFIG_PATH):
        save_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SETTINGS)

    merged = dict(DEFAULT_SETTINGS)
    merged.update(data)
    return merged


def save_settings(settings: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=4)


def update_setting(key: str, value) -> dict:
    settings = load_settings()
    settings[key] = value
    save_settings(settings)
    return settings
