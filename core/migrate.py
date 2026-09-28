"""Одноразова міграція старого об'єднаного config.json у settings.json
(налаштування, керовані у вкладці «Налаштування») + data.json (runtime-дані:
історія тестів мережі, бекапи твіків, автозапуск тощо).

Викликається лінькво з core.settings.load_settings() і core.app_data.load_data()
(idempotent — реальна робота відбувається щонайбільше один раз за процес,
і лише якщо старий config.json є, а нових файлів ще нема)."""

import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LEGACY_CONFIG_PATH = os.path.join(_ROOT, "config.json")
_SETTINGS_PATH = os.path.join(_ROOT, "settings.json")
_DATA_PATH = os.path.join(_ROOT, "data.json")

# Ключі старого config.json, що є runtime-даними (переносяться в data.json);
# усе інше вважається налаштуванням і переноситься в settings.json.
_DATA_KEYS = (
    "last_tab",
    "network_test_history",
    "autostart_disabled",
    "registry_tweaks_initial_state",
    "registry_tweaks_backup_done",
    "game_mode",
)

_done = False


def migrate_if_needed() -> None:
    global _done
    if _done:
        return
    _done = True

    if not os.path.exists(_LEGACY_CONFIG_PATH):
        return
    if os.path.exists(_SETTINGS_PATH) or os.path.exists(_DATA_PATH):
        return

    try:
        with open(_LEGACY_CONFIG_PATH, "r", encoding="utf-8") as f:
            legacy = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    settings = {k: v for k, v in legacy.items() if k not in _DATA_KEYS}
    data = {k: v for k, v in legacy.items() if k in _DATA_KEYS}

    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)
        with open(_DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(_LEGACY_CONFIG_PATH, _LEGACY_CONFIG_PATH + ".bak")
    except OSError:
        pass
