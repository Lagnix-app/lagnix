"""Пошук великих файлів у типових користувацьких папках для вкладки «Очищення».

Лише сканує й повертає список — нічого не видаляє.
"""

import os

DEFAULT_MIN_SIZE_BYTES = 500 * 1024 * 1024

_SCAN_FOLDER_NAMES = ("Downloads", "Videos", "Desktop", "Documents")


def get_scan_roots() -> list[str]:
    """Існуючі папки Downloads/Videos/Desktop/Documents у профілі користувача.

    Додатково перевіряє ці самі назви під %OneDrive%, бо там їх часто
    переносить синхронізація OneDrive.
    """
    home = os.path.expanduser("~")
    roots = []
    seen = set()

    def _add(base: str):
        for name in _SCAN_FOLDER_NAMES:
            path = os.path.abspath(os.path.join(base, name))
            if os.path.isdir(path):
                key = os.path.normcase(path)
                if key not in seen:
                    seen.add(key)
                    roots.append(path)

    _add(home)

    onedrive = os.environ.get("OneDrive")
    if onedrive:
        _add(onedrive)

    return roots


def scan_large_files(min_size_bytes: int = DEFAULT_MIN_SIZE_BYTES, progress_cb=None, stop_event=None) -> list[dict]:
    """Рекурсивно шукає файли розміром від min_size_bytes у get_scan_roots().

    progress_cb(entry) викликається одразу після кожної знахідки, щоб фоновий
    потік міг прогресивно оновлювати список без очікування завершення всього
    сканування. stop_event дозволяє перервати сканування достроково.
    """
    found = []

    for root_path in get_scan_roots():
        for root, _dirs, files in os.walk(root_path, onerror=lambda e: None):
            if stop_event is not None and stop_event.is_set():
                return found

            for name in files:
                file_path = os.path.join(root, name)
                try:
                    size = os.path.getsize(file_path)
                except OSError:
                    continue

                if size < min_size_bytes:
                    continue

                entry = {"path": file_path, "size_bytes": size, "root": root_path}
                found.append(entry)
                if progress_cb:
                    progress_cb(entry)

    found.sort(key=lambda e: e["size_bytes"], reverse=True)
    return found


def open_containing_folder(file_path: str) -> bool:
    """Відкриває провідник у папці, що містить файл. Нічого не видаляє."""
    folder = os.path.dirname(file_path)
    if not os.path.isdir(folder):
        return False
    try:
        os.startfile(folder)
        return True
    except OSError:
        return False
