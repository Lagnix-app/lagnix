"""Завантаження та збереження налаштувань Lagnix (settings.json) — усе,
що керується з вкладки «Налаштування». Runtime-дані (історія тестів,
бекапи твіків, автозапуск тощо) живуть окремо в core/app_data.py
(data.json), щоб не змішувати те, що редагує користувач, з тим, що
накопичує сама програма. Старий об'єднаний config.json мігрується
автоматично один раз (core/migrate.py)."""

import copy
import json
import os
import threading
import time

from core.hotkeys import DEFAULT_HOTKEYS
from core.i18n import LANGUAGE_CODES, detect_system_language
from core.migrate import migrate_if_needed

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")

DEFAULT_SETTINGS = {
    "theme": "dark",
    # Мова інтерфейсу (core/i18n.py). None — ще не обрана: при першому запуску
    # береться мова Windows (якщо її немає в списку — англійська).
    "language": None,
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
    # Звук наведення — частка від загальної гучності (0.5 = половина).
    "sounds_hover_ratio": 0.5,
    "monitor_update_interval_s": 1.0,
    "animations_enabled": True,
    "robot_animation_enabled": True,
    # "exit" — закривати програму; "tray" — згортати в трей.
    "close_action": "exit",
    # Чи згорнуті пояснення (пінг/джитер/втрати) на вкладці «Мережа».
    "network_help_collapsed": False,
    # «Монітор»: процеси згруповані за програмами (як у Диспетчері завдань).
    "monitor_group_processes": True,
    # «Ігровий режим»: рівень для профілів, де його ще не обирали (core/app_catalog.py),
    # і чи показувати сповіщення з «Скасувати» при автоувімкненні.
    "game_mode_default_level": "balanced",
    "game_mode_auto_toast": True,
    # Сповіщення Windows (через іконку в треї): автоувімкнення Ігрового режиму
    # і перегрів CPU/GPU (поріг — temp_threshold_c, не частіше ніж раз на 5 хв).
    "notify_game_mode": True,
    "notify_overheat": True,
    # Оверлей поверх ігор (ui/overlay.py). overlay_position — [x, y] після
    # перетягування (Ctrl + миша); None — від кута overlay_corner.
    "overlay_enabled": False,
    "overlay_size": "small",
    "overlay_opacity": 0.85,
    "overlay_metrics": {"cpu": True, "gpu": True, "ram": True, "gpu_temp": True, "cpu_temp": True},
    "overlay_corner": "top_left",
    "overlay_position": None,
    # Глобальні гарячі клавіші (core/hotkeys.py); порожній рядок — вимкнено.
    "hotkeys": dict(DEFAULT_HOTKEYS),
}


# Кеш розібраного settings.json за (mtime, розмір): load_settings() кличуть
# часто (Монітор — щосекунди з фонового потоку), а файл змінюється рідко.
# Після ВЛАСНОГО запису кеш оновлюється напряму: раніше два записи в межах
# одного тіку годинника файлової системи з однаковим розміром файлу давали
# той самий ключ, load_settings() повертав застарілий кеш, і наступний
# update_setting() затирав щойно збережене значення старим (так «сама»
# скидалась гучність). Запис — атомарний (тимчасовий файл + os.replace) і під
# блокуванням, тож читач із фонового потоку не бачить напівзаписаний файл.
_lock = threading.RLock()
_cache_key = None
_cache_data: dict | None = None


def _stat_key():
    st = os.stat(SETTINGS_PATH)
    return st.st_mtime_ns, st.st_size


def load_settings() -> dict:
    global _cache_key, _cache_data
    migrate_if_needed()

    with _lock:
        try:
            key = _stat_key()
        except OSError:
            first = copy.deepcopy(DEFAULT_SETTINGS)
            first["language"] = detect_system_language()  # перший запуск — мова Windows
            save_settings(first)
            return copy.deepcopy(first)

        if key != _cache_key or _cache_data is None:
            try:
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                return copy.deepcopy(_cache_data if _cache_data is not None else DEFAULT_SETTINGS)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            if merged.get("language") not in LANGUAGE_CODES:
                merged["language"] = detect_system_language()
            _cache_key, _cache_data = key, merged
        return copy.deepcopy(_cache_data)  # копія: виклики можуть змінювати словник


def save_settings(settings: dict) -> None:
    global _cache_key, _cache_data
    with _lock:
        tmp_path = SETTINGS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)
        for attempt in range(5):  # антивірус/індексатор може на мить тримати файл
            try:
                os.replace(tmp_path, SETTINGS_PATH)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(copy.deepcopy(settings))
        _cache_data = merged
        try:
            _cache_key = _stat_key()
        except OSError:
            _cache_key = None


def update_setting(key: str, value) -> dict:
    with _lock:  # читання й запис разом — інакше паралельне оновлення загубиться
        settings = load_settings()
        settings[key] = value
        save_settings(settings)
        return settings


def reset_to_defaults() -> dict:
    """Скидає лише settings.json (загальні налаштування) — не чіпає
    data.json (історія тестів, збережені початкові значення твіків,
    вимкнені записи автозапуску тощо). Мова інтерфейсу лишається тією, що є:
    скидання не має раптом перемикати мову, якою користувач читає програму."""
    defaults = copy.deepcopy(DEFAULT_SETTINGS)
    defaults["language"] = load_settings().get("language")
    save_settings(defaults)
    return copy.deepcopy(defaults)
