"""Посилання Lagnix. Порожнє посилання — відповідні кнопки в інтерфейсі не показуються."""

import webbrowser

KOFI_URL = "https://ko-fi.com/lagnix"
ITCH_URL = "https://lagnixapp.itch.io/lagnix"
GITHUB_URL = "https://github.com/Lagnix-app/lagnix"
ISSUES_URL = "https://github.com/Lagnix-app/lagnix/issues"


def open_link(url: str) -> bool:
    """Відкриває посилання в браузері за замовчуванням (лише http/https)."""
    if not url or not url.startswith(("https://", "http://")):
        return False
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False
