"""Браузери: чи ввімкнене в них відновлення сесії («продовжити, де зупинився»).

Потрібно перед примусовим закриттям браузера в Ігровому режимі: якщо сесія
відновлюється, вкладки повернуться при наступному запуску й закрити примусово
безпечно; якщо ні — користувача попереджаємо. Лише читання файлів налаштувань."""

import glob
import json
import os
import re

# exe (нижній регістр) -> (тип, теки профілів відносно %LOCALAPPDATA% / %APPDATA%)
_LOCAL, _ROAMING = "LOCALAPPDATA", "APPDATA"
_CHROMIUM = {
    "chrome.exe": (_LOCAL, r"Google\Chrome\User Data", False),
    "msedge.exe": (_LOCAL, r"Microsoft\Edge\User Data", False),
    "brave.exe": (_LOCAL, r"BraveSoftware\Brave-Browser\User Data", False),
    "vivaldi.exe": (_LOCAL, r"Vivaldi\User Data", False),
    "browser.exe": (_LOCAL, r"Yandex\YandexBrowser\User Data", False),    # Яндекс
    "opera.exe": (_ROAMING, r"Opera Software\Opera Stable", True),          # профіль прямо в теці
}
_FIREFOX = {"firefox.exe", "waterfox.exe", "librewolf.exe", "floorp.exe"}
_RESTORE_LAST_SESSION = 1   # session.restore_on_startup: «продовжити, де зупинився»


def _chromium_profiles(root: str, flat: bool) -> list[str]:
    if flat:
        return [os.path.join(root, "Preferences")]
    return [os.path.join(root, "Default", "Preferences")] + glob.glob(os.path.join(root, "Profile *", "Preferences"))


def session_restore(exe_name: str) -> bool | None:
    """True — відновлення сесії ввімкнене (в усіх профілях), False — ні, None — невідомо."""
    low = exe_name.lower()
    try:
        if low in _CHROMIUM:
            base, sub, flat = _CHROMIUM[low]
            root = os.path.join(os.environ.get(base, ""), sub)
            files = [f for f in _chromium_profiles(root, flat) if os.path.isfile(f)]
            if not files:
                return None
            states = []
            for path in files:
                with open(path, encoding="utf-8") as f:
                    prefs = json.load(f)
                value = (prefs.get("session") or {}).get("restore_on_startup")
                states.append(value == _RESTORE_LAST_SESSION)
            return all(states)
        if low in _FIREFOX:
            profiles = glob.glob(os.path.join(os.environ.get(_ROAMING, ""), "Mozilla", "Firefox", "Profiles", "*", "prefs.js"))
            if not profiles:
                return None
            pattern = re.compile(r'user_pref\("browser\.startup\.page",\s*3\)')
            newest = max(profiles, key=os.path.getmtime)  # профіль, яким користувались останнім
            with open(newest, encoding="utf-8", errors="replace") as f:
                return bool(pattern.search(f.read()))
    except (OSError, ValueError):
        return None
    return None
