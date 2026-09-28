"""Іконка PulseFPS у системному треї (pystray) — використовується, коли
користувач згортає вікно замість закриття (налаштування «При закритті
вікна») або коли програма запущена автозапуском Windows мінімізованою.

pystray веде власний GUI-цикл на окремому потоці (win32/GTK/AppIndicator
залежно від платформи); тому колбеки меню (_handle_show/_handle_exit)
не можуть напряму чіпати tkinter-віджети — вони йдуть через callback,
який власник (MainWindow) обгортає в self.after(0, ...), так само як
фонові потоки монітора/мережі вже роблять у цьому проєкті."""

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
    _logger.error("pystray недоступний — згортання в трей вимкнене (pip install pystray)")

_ICON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "pulsefps-icon-48.png"
)


def is_available() -> bool:
    return _HAS_PYSTRAY


class TrayIcon:
    """Один трей-значок на процес: show()/hide() можна викликати скільки
    завгодно разів — повторні виклики без ефекту, якщо стан уже такий."""

    def __init__(self, on_open, on_exit):
        self._on_open = on_open
        self._on_exit = on_exit
        self._icon = None

    def show(self) -> None:
        if not _HAS_PYSTRAY or self._icon is not None:
            return
        try:
            image = (
                Image.open(_ICON_PATH)
                if os.path.exists(_ICON_PATH)
                else Image.new("RGB", (32, 32), "#2ee59d")
            )
            menu = pystray.Menu(
                pystray.MenuItem("Відкрити PulseFPS", self._handle_open, default=True),
                pystray.MenuItem("Вихід", self._handle_exit),
            )
            icon = pystray.Icon("PulseFPS", image, "PulseFPS", menu)
            self._icon = icon
            icon.run_detached()
        except Exception:
            _logger.exception("Не вдалося показати іконку в треї")
            self._icon = None

    def hide(self) -> None:
        if self._icon is None:
            return
        try:
            self._icon.stop()
        except Exception:
            _logger.exception("Не вдалося прибрати іконку з трею")
        self._icon = None

    def _handle_open(self, _icon=None, _item=None) -> None:
        self._on_open()

    def _handle_exit(self, _icon=None, _item=None) -> None:
        self._on_exit()
