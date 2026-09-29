"""Плани живлення Windows і власний план «PulseFPS Ultra».

«PulseFPS Ultra» створюється один раз дублюванням прихованого плану
«Максимальна продуктивність» (Ultimate Performance), а якщо його немає
(звичайно на ноутбуках із Modern Standby) — «Високої продуктивності».
GUID плану зберігається в data.json (game_mode.ultra_guid), тож дублі не
плодяться: перед створенням перевіряємо, що збережений план ще існує.

Назви планів у `powercfg` локалізовані, тому скрізь працюємо з GUID.
"""

from __future__ import annotations

import re
import subprocess

import psutil

from core.logging_setup import get_logger

_logger = get_logger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ULTRA_NAME = "PulseFPS Ultra"
ULTRA_DESCRIPTION = "Максимальна продуктивність від PulseFPS: без паркування ядер і енергозбереження."
ULTIMATE_SOURCE_GUID = "e9a42b02-d5df-448d-aa00-03f14749eb61"
HIGH_PERFORMANCE_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
BALANCED_GUID = "381b4222-f694-41f0-9685-ff5bb260df2e"

_GUID_RE = re.compile(r"([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})")

# (підгрупа, параметр, значення, опис) — налаштування «від мережі» (AC)
_SUB_PROCESSOR = "54533251-82be-4824-96c1-47b60b740d00"
_SUB_USB = "2a737441-1930-4402-8d77-b2bebba308a3"
_SUB_PCIE = "501a4d13-42af-4429-9fd1-a8218c268e20"
_SUB_DISK = "0012ee47-9041-4b5d-9b77-535fba8b1442"
_SUB_SLEEP = "238c9fa8-0aad-41ed-83f4-97be242c8f20"
ULTRA_SETTINGS = (
    (_SUB_PROCESSOR, "893dee8e-2bef-41e0-89c6-b55d0929964c", 100, "мін. стан процесора"),
    (_SUB_PROCESSOR, "bc5038f7-23e0-4960-96da-33abaf5935ec", 100, "макс. стан процесора"),
    (_SUB_PROCESSOR, "0cc5b647-c1df-4637-891a-dec35c318583", 100, "паркування ядер вимкнено"),
    (_SUB_PROCESSOR, "be337238-0d82-4146-a960-4f3749d470c7", 2, "агресивне підвищення продуктивності"),
    (_SUB_USB, "48e6b7a6-50f5-4782-a5d4-53bb8f07e226", 0, "USB selective suspend"),
    (_SUB_PCIE, "ee12f906-d277-404b-b6da-e5fa1a576df5", 0, "PCIe Link State Power Management"),
    (_SUB_DISK, "6738e2c4-e8a5-4a42-b16a-e040e769756e", 0, "вимкнення диска"),
    (_SUB_SLEEP, "29f6c1db-86da-48c5-9fdb-f2b67b1f44da", 0, "сон"),
    (_SUB_SLEEP, "9d7815a6-7ee4-497e-8888-515a05f02364", 0, "гібернація"),
)


def _powercfg(*args: str, timeout: int = 8) -> tuple[bool, str]:
    """(успіх, stdout+stderr) — кодування консолі Windows невідоме, тому декодуємо
    з ignore: нам потрібні лише GUID-и."""
    try:
        result = subprocess.run(
            ["powercfg", *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, creationflags=_NO_WINDOW,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)
    return result.returncode == 0, result.stdout.decode("utf-8", errors="ignore")


def get_active_scheme() -> str | None:
    ok, out = _powercfg("/getactivescheme", timeout=3)
    match = _GUID_RE.search(out) if ok else None
    return match.group(1).lower() if match else None


def set_active_scheme(guid: str) -> tuple[bool, str]:
    if not guid:
        return True, ""
    ok, out = _powercfg("/setactive", guid, timeout=5)
    return ok, "" if ok else out.strip()


def list_schemes() -> list[str]:
    """GUID усіх існуючих планів (у нижньому регістрі)."""
    ok, out = _powercfg("/list", timeout=5)
    return [m.lower() for m in _GUID_RE.findall(out)] if ok else []


def scheme_exists(guid: str | None) -> bool:
    return bool(guid) and guid.lower() in list_schemes()


def _duplicate(source: str) -> str | None:
    ok, out = _powercfg("-duplicatescheme", source)
    match = _GUID_RE.search(out) if ok else None
    return match.group(1).lower() if match else None


def _apply_ultra_settings(guid: str) -> list[str]:
    """Налаштовує план; повертає описи параметрів, які не вдалося виставити
    (частина параметрів існує не на всіх ПК — це не помилка створення плану)."""
    failed = []
    for subgroup, setting, value, label in ULTRA_SETTINGS:
        ok, out = _powercfg("/setacvalueindex", guid, subgroup, setting, str(value))
        if not ok:
            failed.append(label)
            _logger.warning("PulseFPS Ultra: не вдалося виставити «%s»: %s", label, out.strip())
    return failed


def ensure_ultra(saved_guid: str | None) -> tuple[str | None, str]:
    """Гарантує наявність плану «PulseFPS Ultra». -> (GUID або None, повідомлення
    про помилку). Існуючий збережений план не перестворюється (і не
    переналаштовується — користувач міг щось підкрутити)."""
    if saved_guid and scheme_exists(saved_guid):
        return saved_guid.lower(), ""

    schemes = list_schemes()
    guid = None
    for source in (ULTIMATE_SOURCE_GUID, HIGH_PERFORMANCE_GUID):
        # «Максимальна продуктивність» прихована: powercfg /list її не показує,
        # тому пробуємо дублювати напряму; «Висока» може бути видалена
        if source == HIGH_PERFORMANCE_GUID and source not in schemes:
            continue
        guid = _duplicate(source)
        if guid:
            break
    if not guid:
        return None, "Не вдалося створити план живлення (немає прав або схеми недоступні)."

    _powercfg("/changename", guid, ULTRA_NAME, ULTRA_DESCRIPTION)
    failed = _apply_ultra_settings(guid)
    if failed:
        _logger.info("PulseFPS Ultra створено; не підтримуються: %s", ", ".join(failed))
    return guid, ""


def delete_scheme(guid: str) -> tuple[bool, str]:
    """Видаляє план. Активний план видалити не можна — спершу перемикаємо на «Збалансований»."""
    if get_active_scheme() == (guid or "").lower():
        set_active_scheme(BALANCED_GUID)
    ok, out = _powercfg("/delete", guid)
    return ok, "" if ok else out.strip()


def on_battery() -> bool:
    """True, якщо це ноутбук і він зараз працює від батареї."""
    try:
        battery = psutil.sensors_battery()
    except (AttributeError, NotImplementedError, OSError):
        return False
    return bool(battery is not None and battery.power_plugged is False)


def has_battery() -> bool:
    try:
        return psutil.sensors_battery() is not None
    except (AttributeError, NotImplementedError, OSError):
        return False
