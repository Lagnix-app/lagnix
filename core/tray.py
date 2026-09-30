"""Іконка PulseFPS у системному треї (pystray): видима весь час роботи програми.
Меню — «Відкрити PulseFPS», «Ігровий режим» і «Оверлей» (з галочкою стану),
«Швидке очищення тимчасових файлів», «Вихід»; подвійний клік — відкрити вікно.
Через цю ж іконку показуються сповіщення Windows (notify) — на Windows 10/11
вони з'являються як звичайні toast-сповіщення.

pystray веде власний цикл повідомлень на окремому потоці; колбеки меню й
функції checked виконуються в ньому. Тому колбеки не чіпають tkinter — власник
(ui/app_shell.py) передає їх в інтерфейс через ui/bg.py ui_call, а checked
читає лише звичайні булеві значення."""

import os

from core.logging_setup import get_logger

_logger = get_logger(__name__)

try:
    import pystray
    from PIL import Image
    _HAS_PYSTRAY = True
except ImportError:
    pystray = None
    Image = None
    _HAS_PYSTRAY = False
    _logger.error("pystray недоступний — іконка в треї і сповіщення Windows вимкнені (pip install pystray)")

_ICON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "pulsefps-icon-48.png"
)

_WM_LBUTTONUP = 0x0202
_WM_LBUTTONDBLCLK = 0x0203


def is_available() -> bool:
    return _HAS_PYSTRAY


def _icon_class():
    """pystray.Icon, у якому вікно відкривається подвійним кліком (а не одинарним,
    як у pystray за замовчуванням) — лише для бекенда win32."""
    base = pystray.Icon
    if not hasattr(base, "_on_notify"):
        return base

    class _Icon(base):
        def _on_notify(self, wparam, lparam):
            if lparam == _WM_LBUTTONDBLCLK:
                self()  # дія пункту default=True
                return None
            if lparam == _WM_LBUTTONUP:
                return None
            return super()._on_notify(wparam, lparam)

    return _Icon


class TrayIcon:
    """Один трей-значок на процес. show()/stop() ідемпотентні.

    actions: {"open", "game_mode", "overlay", "cleanup", "exit"} -> колбек (потік pystray);
    checked: {"game_mode", "overlay"} -> функція без аргументів, що повертає bool."""

    def __init__(self, actions: dict, checked: dict):
        self._actions = actions
        self._checked = checked
        self._icon = None
        self._image = None

    @property
    def visible(self) -> bool:
        return self._icon is not None

    def show(self, image=None) -> None:
        if image is not None:
            self._image = image
        if not _HAS_PYSTRAY or self._icon is not None:
            return
        try:
            icon = _icon_class()("PulseFPS", self._image or self._fallback_image(), "PulseFPS", self._menu())
            self._icon = icon
            icon.run_detached()
        except Exception:
            _logger.exception("Не вдалося показати іконку в треї")
            self._icon = None

    def stop(self) -> None:
        icon, self._icon = self._icon, None
        if icon is None:
            return
        try:
            icon.stop()
        except Exception:
            _logger.exception("Не вдалося прибрати іконку з трею")

    def set_image(self, image) -> None:
        self._image = image
        if self._icon is not None:
            try:
                self._icon.icon = image
            except Exception:
                _logger.exception("Не вдалося оновити іконку в треї")

    def refresh_menu(self) -> None:
        """Перебудувати меню (галочки): pystray на Windows збирає його заздалегідь."""
        if self._icon is not None:
            try:
                self._icon.update_menu()
            except Exception:
                _logger.exception("Не вдалося оновити меню трею")

    def notify(self, title: str, message: str) -> bool:
        """Сповіщення Windows від імені іконки. False — трею немає."""
        if self._icon is None:
            return False
        try:
            self._icon.notify(message, title)
            return True
        except Exception:
            _logger.exception("Не вдалося показати сповіщення «%s»", title)
            return False

    # ------------------------------------------------------------ внутрішнє

    def _fallback_image(self):
        if os.path.exists(_ICON_PATH):
            return Image.open(_ICON_PATH)
        return Image.new("RGB", (32, 32), "#2ee59d")

    def _call(self, key: str):
        def handler(_icon=None, _item=None):
            callback = self._actions.get(key)
            if callback is not None:
                callback()
        return handler

    def _is_checked(self, key: str):
        def checked(_item=None):
            try:
                return bool(self._checked[key]())
            except Exception:
                return False
        return checked

    def _menu(self):
        item = pystray.MenuItem
        return pystray.Menu(
            item("Відкрити PulseFPS", self._call("open"), default=True),
            pystray.Menu.SEPARATOR,
            item("Ігровий режим", self._call("game_mode"), checked=self._is_checked("game_mode")),
            item("Оверлей", self._call("overlay"), checked=self._is_checked("overlay")),
            pystray.Menu.SEPARATOR,
            item("Швидке очищення тимчасових файлів", self._call("cleanup")),
            pystray.Menu.SEPARATOR,
            item("Вихід", self._call("exit")),
        )
