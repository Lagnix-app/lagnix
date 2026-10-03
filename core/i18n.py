"""Переклади інтерфейсу PulseFPS: locales/<код>.json, t("ключ", **параметри).

Значення в JSON — рядок-шаблон для str.format ("Знайдено {count} файлів") або,
для множини, словник форм за категоріями CLDR:
    {"one": "{count} файл", "few": "{count} файли", "many": "{count} файлів",
     "other": "{count} файлу"}
Форму обирає параметр count (див. plural_category). Якщо ключа немає в
поточній мові — береться англійський варіант (а якщо немає й там — сам ключ);
у режимі DEBUG (змінна оточення PULSEFPS_DEBUG=1 або аргумент --debug) кожен
такий пропуск один раз пишеться в logs.txt.

t() можна кликати з будь-якого потоку: таблиці лише читаються, а перемикання
мови підміняє посилання на словник цілком.
"""

import json
import locale
import os
import sys
import threading
from collections.abc import Mapping

from core.logging_setup import get_logger

LOCALES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "locales")

# (код — він же ім'я locales/<код>.json і assets/flags/<код>.png, назва мови рідною мовою)
LANGUAGES: tuple[tuple[str, str], ...] = (
    ("uk", "Українська"),
    ("en", "English"),
    ("pl", "Polski"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("pt-BR", "Português (Brasil)"),
    ("fr", "Français"),
    ("tr", "Türkçe"),
    ("ru", "Русский"),
    ("zh-CN", "简体中文"),
    ("ja", "日本語"),
    ("ko", "한국어"),
)
LANGUAGE_CODES = tuple(code for code, _name in LANGUAGES)
FALLBACK = "en"

DEBUG = os.environ.get("PULSEFPS_DEBUG") == "1" or "--debug" in sys.argv[1:]

_log = get_logger("core.i18n")
_lock = threading.Lock()
_tables: dict[str, dict] = {}
_current = FALLBACK
_current_table: dict = {}
_fallback_table: dict = {}
_reported_missing: set[tuple[str, str]] = set()
_listeners: list = []


# ------------------------------------------------------------------ множина

def plural_category(lang: str, n) -> str:
    """Категорія множини CLDR для числа n: one / few / many / other."""
    try:
        is_int = float(n).is_integer()
        i = abs(int(n))
    except (TypeError, ValueError):
        return "other"
    if lang in ("zh-CN", "ja", "ko"):
        return "other"
    if lang in ("uk", "ru"):
        if not is_int:
            return "other"
        if i % 10 == 1 and i % 100 != 11:
            return "one"
        if 2 <= i % 10 <= 4 and not 12 <= i % 100 <= 14:
            return "few"
        return "many"
    if lang == "pl":
        if not is_int:
            return "other"
        if i == 1:
            return "one"
        if 2 <= i % 10 <= 4 and not 12 <= i % 100 <= 14:
            return "few"
        return "many"
    if lang in ("fr", "pt-BR"):
        return "one" if is_int and i in (0, 1) else "other"
    # en, de, es, tr
    return "one" if is_int and i == 1 else "other"


def _pick_plural(forms: dict, lang: str, count) -> str:
    category = plural_category(lang, count)
    for key in (category, "other", "many", "one"):
        if key in forms:
            return forms[key]
    return next(iter(forms.values()), "")


# --------------------------------------------------------------- завантаження

def _load_table(code: str) -> dict:
    with _lock:
        table = _tables.get(code)
        if table is None:
            path = os.path.join(LOCALES_DIR, f"{code}.json")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    table = json.load(f)
            except (OSError, json.JSONDecodeError):
                _log.exception("Failed to load locale file %s", path)
                table = {}
            _tables[code] = table
        return table


def get_language() -> str:
    return _current


def language_name(code: str) -> str:
    return dict(LANGUAGES).get(code, code)


def set_language(code: str) -> None:
    """Перемикає мову й сповіщає слухачів (on_change) — лише з потоку інтерфейсу."""
    global _current, _current_table, _fallback_table
    if code not in LANGUAGE_CODES:
        code = FALLBACK
    _fallback_table = _load_table(FALLBACK)
    _current_table = _load_table(code)
    changed = code != _current
    _current = code
    if changed:
        for callback in list(_listeners):
            try:
                callback(code)
            except Exception:
                _log.exception("Language change listener failed")


def on_change(callback) -> None:
    """callback(код_мови) після кожної зміни мови."""
    if callback not in _listeners:
        _listeners.append(callback)


def remove_listener(callback) -> None:
    if callback in _listeners:
        _listeners.remove(callback)


def detect_system_language() -> str:
    """Мова інтерфейсу Windows -> код зі списку LANGUAGES (інакше en)."""
    tag = ""
    if sys.platform == "win32":
        try:
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            tag = locale.windows_locale.get(lang_id, "")
        except (AttributeError, OSError):
            tag = ""
    if not tag:
        try:
            tag = locale.getlocale()[0] or ""
        except ValueError:
            tag = ""
    return match_language(tag)


def match_language(tag: str) -> str:
    """"pt_BR" / "de-AT" / "zh_TW" -> найближчий код зі списку, інакше en."""
    tag = (tag or "").replace("-", "_").lower()
    if not tag:
        return FALLBACK
    primary = tag.split("_")[0]
    if primary == "pt":
        return "pt-BR"
    if primary == "zh":
        return "zh-CN"
    if primary == "nb" or primary == "no":
        return FALLBACK
    for code in LANGUAGE_CODES:
        if code.lower().split("-")[0] == primary:
            return code
    return FALLBACK


# ------------------------------------------------------------------- переклад

_FONT_FAMILIES = {"zh-CN": "Microsoft YaHei UI", "ja": "Yu Gothic UI", "ko": "Malgun Gothic"}


def ui_font_family(code: str | None = None) -> str:
    """Шрифт інтерфейсу для мови: для китайської, японської й корейської — з
    підтримкою їхніх символів (інакше на місці ієрогліфів — квадратики)."""
    return _FONT_FAMILIES.get(code or _current, "Segoe UI")


def has_key(key: str) -> bool:
    return key in _current_table or key in _fallback_table


def t(key: str, **params) -> str:
    """Переклад ключа поточною мовою з підставленими параметрами."""
    lang = _current
    value = _current_table.get(key)
    if value is None:
        if DEBUG and (lang, key) not in _reported_missing:
            _reported_missing.add((lang, key))
            _log.error("Missing translation: [%s] %s", lang, key)
        value = _fallback_table.get(key)
        lang = FALLBACK
        if value is None:
            return key
    if isinstance(value, dict):
        value = _pick_plural(value, lang, params.get("count", 0))
    if not params:
        return value
    try:
        return value.format(**params)
    except (KeyError, IndexError, ValueError) as exc:
        if DEBUG:
            _log.error("Bad translation template [%s] %s: %s", lang, key, exc)
        return value


def maybe_t(text: str) -> str:
    """t(text), якщо text — відомий ключ; інакше сам text (торгові назви, "CPU" тощо)."""
    return t(text) if has_key(text) else text


def tlist(prefix: str, keys) -> list[str]:
    """[t(prefix + k) for k in keys] — для списків варіантів у випадних меню."""
    return [t(prefix + k) for k in keys]


class TDict(Mapping):
    """Словник «значення -> ключ перекладу», що віддає текст поточною мовою:
    LEVEL_LABELS[lv] щоразу — t(ключ). Для таблиць підписів на рівні модуля,
    які інакше «застигли» б мовою, активною під час імпорту."""

    def __init__(self, keys: dict, passthrough: bool = False):
        self._keys = dict(keys)
        self._passthrough = passthrough  # значення, що не є ключем (торгова назва), — як є

    def __getitem__(self, item) -> str:
        key = self._keys[item]
        if self._passthrough and not has_key(key):
            return key
        return t(key)

    def __iter__(self):
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def key_of(self, item) -> str:
        return self._keys[item]


# мова за замовчуванням до читання налаштувань (main.py викликає set_language)
_fallback_table = _load_table(FALLBACK)
_current_table = _fallback_table
