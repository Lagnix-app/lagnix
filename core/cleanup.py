"""Сканування та очищення тимчасових файлів Windows для вкладки «Очищення».

Видалення завжди відбувається за ключем цілі з get_targets() — довільний шлях
ззовні не приймається, тож очищення обмежене лише %TEMP% і C:\\Windows\\Temp.
"""

import os
import tempfile

WINDOWS_TEMP_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp")


def get_targets() -> list[dict]:
    """Список цілей очищення: user-temp завжди, Windows\\Temp — лише якщо є доступ на читання."""
    targets = [{
        "key": "user_temp",
        "label": "Тимчасові файли користувача (%TEMP%)",
        "path": os.path.abspath(tempfile.gettempdir()),
    }]

    if os.path.isdir(WINDOWS_TEMP_PATH) and os.access(WINDOWS_TEMP_PATH, os.R_OK):
        targets.append({
            "key": "windows_temp",
            "label": f"Тимчасові файли Windows ({WINDOWS_TEMP_PATH})",
            "path": os.path.abspath(WINDOWS_TEMP_PATH),
        })

    return targets


def _target_path(key: str) -> str | None:
    for target in get_targets():
        if target["key"] == key:
            return target["path"]
    return None


def scan_target(key: str) -> dict:
    """Рахує сумарний розмір і кількість файлів цілі. Недоступні файли просто пропускаються."""
    path = _target_path(key)
    if path is None:
        return {"key": key, "path": None, "exists": False, "size_bytes": 0, "file_count": 0}

    total_size = 0
    file_count = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for name in files:
            try:
                total_size += os.path.getsize(os.path.join(root, name))
                file_count += 1
            except OSError:
                continue

    return {"key": key, "path": path, "exists": True, "size_bytes": total_size, "file_count": file_count}


def clean_target(key: str) -> dict:
    """Видаляє файли й порожні підпапки всередині цілі; саму кореневу папку не чіпає.

    Зайняті або захищені файли, які не вдається видалити, тихо пропускаються.
    """
    path = _target_path(key)
    if path is None:
        return {"key": key, "freed_bytes": 0, "deleted_count": 0, "skipped_count": 0}

    freed_bytes = 0
    deleted_count = 0
    skipped_count = 0

    for root, _dirs, files in os.walk(path, topdown=False, onerror=lambda e: None):
        for name in files:
            file_path = os.path.join(root, name)
            try:
                size = os.path.getsize(file_path)
                os.remove(file_path)
                freed_bytes += size
                deleted_count += 1
            except OSError:
                skipped_count += 1

        if root != path:
            try:
                os.rmdir(root)
            except OSError:
                pass

    return {"key": key, "freed_bytes": freed_bytes, "deleted_count": deleted_count, "skipped_count": skipped_count}


def format_size(size_bytes: int) -> str:
    """Форматує розмір у байтах у зручний вигляд (Б/КБ/МБ/ГБ/ТБ)."""
    size = float(size_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ТБ"
