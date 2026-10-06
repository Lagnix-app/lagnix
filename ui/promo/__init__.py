r"""Прихований режим `python main.py --promo <сценарій>[:мова]` — запис кадрів для промо-відео TikTok.

Сценарії (ui/promo/scenarios.py): `sad_robot`, `one_click`. Мова за замовчуванням — en
(`one_click:uk` — інтерфейс програми українською). Як і `--screenshots`: окрема тимчасова тека
даних (core/paths.py), без single-instance, автозапуску, звуків і прав адміністратора; усе, що
показується, — імітація (ui/promo/fakes.py). Кадри й `timeline.json` лягають у promo/_work/<сценарій>_<мова>/;
монтує їх tools/promo_render.py.

Приватність: у кадрах лише вигадані дані; на всяк випадок видимі тексти проходять через
ui/screenshot_mode.scrub (ім'я користувача -> User, ПК -> PC, C:\Users\... без імені, IP)."""

from __future__ import annotations

import json
import os

import customtkinter as ctk

from core import i18n, paths, sensors

SCENARIOS = ("sad_robot", "one_click")
WORK_ROOT = os.path.join(paths.RESOURCE_DIR, "promo", "_work")
_THEME_PATH = paths.resource("assets", "lagnix_theme.json")


def _seed_settings(lang: str) -> None:
    data = {"language": lang, "sounds_enabled": False, "overlay_enabled": False, "launch_on_windows": False,
            "startup_tab_mode": "monitor", "window": {"width": 1000, "height": 920}}
    with open(paths.user_file("settings.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)
    for name in ("data.json", "game_mode.json"):
        try:
            os.remove(paths.user_file(name))
        except OSError:
            pass


def run(spec: str) -> None:
    name, _, lang = spec.partition(":")
    lang = lang or "en"
    if name not in SCENARIOS:
        raise SystemExit(f"Невідомий сценарій «{name}». Доступні: {', '.join(SCENARIOS)}")
    _seed_settings(lang)
    i18n.set_language(lang)
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme(_THEME_PATH if os.path.exists(_THEME_PATH) else "blue")
    sensors.set_enabled(False)

    from ui import screenshot_mode
    from ui.promo import engine, scenarios

    root, scenario, layout, ticker = getattr(scenarios, "build_" + name)(lang)
    root.attributes("-topmost", True)
    root.update()
    screenshot_mode._rules[:] = screenshot_mode._build_rules()
    screenshot_mode.scrub_tree(root)

    outdir = os.path.join(WORK_ROOT, f"{name}_{lang}")
    player = engine.Player(root, outdir, screenshot_mode._client_rect, name, lang, layout)
    player.on_tick(ticker)
    try:
        player.run(scenario)
    finally:
        shell = getattr(root, "shell", None)
        if shell is not None:
            shell.shutdown()
        sensors.stop()
        try:
            root.destroy()
        except Exception:
            pass
