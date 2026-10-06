r"""Шляхи Lagnix: ресурси (тільки читання) і дані користувача (запис).

Зі сирців: і те, і те — корінь проєкту. У зібраному Lagnix.exe (PyInstaller):
ресурси — у теці збірки (`sys._MEIPASS`), а settings.json, data.json, logs.txt і
backups/ — у `%APPDATA%/Lagnix` (Program Files захищена від запису)."""

import os
import sys

FROZEN = bool(getattr(sys, "frozen", False))
_SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RESOURCE_DIR = getattr(sys, "_MEIPASS", _SOURCE_ROOT) if FROZEN else _SOURCE_ROOT


def _screenshots_dir() -> str | None:
    """Режим `--screenshots[=мова]`: окрема тека даних (settings/data/logs), щоб не чіпати дані користувача."""
    for arg in sys.argv[1:]:
        if arg == "--screenshots" or arg.startswith("--screenshots="):
            import tempfile
            lang = arg.partition("=")[2] or "all"
            path = os.path.join(tempfile.gettempdir(), f"Lagnix-screenshots-{lang}")
            os.makedirs(path, exist_ok=True)
            return path
    return None


def promo_spec() -> str | None:
    """Режим `--promo <сценарій>[:мова]` або `--promo=<сценарій>[:мова]` (ui/promo): рядок сценарію."""
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg.startswith("--promo="):
            return arg.partition("=")[2] or None
        if arg == "--promo":
            return args[i + 1] if i + 1 < len(args) else None
    return None


def _promo_dir() -> str | None:
    """Окрема тимчасова тека даних для режиму промо (settings/data/logs), як у знімків."""
    spec = promo_spec()
    if spec is None:
        return None
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "Lagnix-promo-" + spec.replace(":", "-").replace("/", "_"))
    os.makedirs(path, exist_ok=True)
    return path


def _user_dir() -> str:
    override = _screenshots_dir() or _promo_dir()
    if override:
        return override
    if not FROZEN:
        return _SOURCE_ROOT
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    path = os.path.join(base, "Lagnix")
    os.makedirs(path, exist_ok=True)
    return path


DATA_DIR = _user_dir()


def resource(*parts: str) -> str:
    return os.path.join(RESOURCE_DIR, *parts)


def user_file(*parts: str) -> str:
    return os.path.join(DATA_DIR, *parts)
