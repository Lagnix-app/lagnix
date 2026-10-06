"""Точка входу Lagnix."""

import ctypes
import os
from core import paths as _paths
import sys


def _enable_dpi_awareness() -> None:
    """Per-monitor DPI awareness до створення будь-якого вікна Tk: без неї
    Windows розтягує вікно (розмито) на екранах із масштабом 125%/150%.
    Малювання (ui/widgets/aa.py) враховує масштаб екрана, тож графіка чітка."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def _ultra_active() -> bool:
    """Ігровий режим увімкнений або активний план живлення — «Lagnix Ultra»."""
    from core import game_mode, power_plans
    state = game_mode.load_game_mode()
    ultra = (state.get("ultra_guid") or "").lower()
    return bool(state.get("is_active")) or bool(ultra and power_plans.get_active_scheme() == ultra)


def _restore_plan() -> bool:
    """Повертає попередній план живлення й прибирає «Lagnix Ultra». -> усе вдалося."""
    from core import game_mode, power_plans
    state = game_mode.load_game_mode()
    ultra = (state.get("ultra_guid") or "").lower()
    previous = state.get("previous_power_plan")
    ok = True
    if state.get("is_active"):
        game_mode.deactivate(state)
        game_mode.save_game_mode(state)
    elif ultra and power_plans.get_active_scheme() == ultra:
        ok = power_plans.set_active_scheme(game_mode.normal_plan_target(previous))[0]
    if ok and ultra and power_plans.scheme_exists(ultra):
        ok = power_plans.delete_scheme(ultra)[0]
    return ok


def _tweaks_cli() -> None:
    """Службові режими без вікна для деінсталятора (installer/Lagnix.iss):
    `--uninstall-state` -> код: біт 1 — є змінені Lagnix твіки, біт 2 — застосовано
    «Lagnix Ultra»/Ігровий режим; `--restore-all` повертає твіки з бекапів і попередній
    план живлення (код 0 — усе гаразд); `--tweaks-pending`/`--restore-tweaks` — лише твіки."""
    args = sys.argv[1:]
    modes = ("--uninstall-state", "--restore-all", "--tweaks-pending", "--restore-tweaks")
    if not any(m in args for m in modes):
        return
    code = 0
    try:
        from core import tweaks
        if "--uninstall-state" in args:
            code = (1 if tweaks.restore_pending() else 0) | (2 if _ultra_active() else 0)
        elif "--tweaks-pending" in args:
            code = 10 if tweaks.restore_pending() else 0
        elif "--restore-all" in args:
            tweaks_ok = all(ok for _, ok, _ in tweaks.restore_tweaks(None))
            code = 0 if (_restore_plan() and tweaks_ok) else 1
        else:
            code = 0 if all(ok for _, ok, _ in tweaks.restore_tweaks(None)) else 1
    except Exception:
        code = 255 if "--uninstall-state" in args else 2
    sys.exit(code)


_tweaks_cli()
_enable_dpi_awareness()

from core import admin as admin_core  # noqa: E402
from core import i18n  # noqa: E402
from core.settings import load_settings  # noqa: E402


def _ensure_admin() -> None:
    """Lagnix завжди працює з правами адміністратора: без них — перезапуск
    через UAC ("runas") і вихід із поточного процесу. Якщо користувач відмовив
    у вікні UAC — пропонуємо спробувати ще раз або вийти."""
    if admin_core.is_admin():
        return
    while True:
        if admin_core.relaunch_as_admin():
            sys.exit(0)
        if not admin_core.ask_retry_admin():
            sys.exit(0)

import customtkinter as ctk  # noqa: E402

from core import launch_on_windows, rename_migrate, restart, sensors, single_instance  # noqa: E402
from core.sounds import ensure_sounds_exist  # noqa: E402
from ui import bg  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

_THEME_PATH = _paths.resource("assets", "lagnix_theme.json")


def _screenshots_main(lang: str) -> None:
    """Прихований режим знімків (ui/screenshot_mode.py): `all` — послідовно en і uk окремими
    процесами; без single-instance, міграцій і автозапуску, дані користувача не чіпає."""
    import subprocess
    from ui import screenshot_mode as shots
    if lang == "all":
        for code in ("en", "uk"):
            subprocess.call([sys.executable, *([] if _paths.FROZEN else [os.path.abspath(__file__)]),
                             f"--screenshots={code}"])
        return
    shots.seed_settings(lang)
    i18n.set_language(lang)
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme(_THEME_PATH if os.path.exists(_THEME_PATH) else "blue")
    sensors.set_enabled(True)
    app = MainWindow()
    runner = shots.Runner(app, shots.outdir_for(lang))
    app.after(1500, runner.start)
    try:
        app.mainloop()
    finally:
        app.shell.shutdown()
        sensors.stop()
    print(f"{lang}: {len(runner.saved)} знімків у {shots.outdir_for(lang)}")


def _promo_main(spec: str) -> None:
    """Прихований режим `--promo=<сценарій>[:мова]` (ui/promo): сам керує інтерфейсом і записує кадри
    для промо-відео. Усе імітація: без прав адміністратора, single-instance, автозапуску й звуків."""
    from ui import promo
    promo.run(spec)


def main():
    promo_spec = _paths.promo_spec()
    if promo_spec:
        _promo_main(promo_spec)
        return
    i18n.set_language(load_settings().get("language"))  # до першого вікна (зокрема запиту прав)
    _ensure_admin()
    from ui import screenshot_mode
    shots_lang = screenshot_mode.language_arg()
    if shots_lang:
        _screenshots_main(shots_lang)
        return
    restart.wait_for_previous()  # перезапуск (зміна мови): старий екземпляр має встигнути вийти
    if not single_instance.acquire():  # Lagnix уже запущено — показуємо його вікно й виходимо
        single_instance.signal_existing()
        return

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme(_THEME_PATH if os.path.exists(_THEME_PATH) else "blue")

    ensure_sounds_exist()
    launch_on_windows.migrate()
    rename_migrate.run()  # PulseFPS -> Lagnix: теки автозапуску, назва плану Ultra
    sensors.set_enabled(load_settings().get("advanced_sensors_enabled", True))

    app = MainWindow()
    restart_state = restart.pop_state()
    if restart_state:
        app.apply_restart_state(restart_state)
    elif "--minimized" in sys.argv[1:]:
        app.start_minimized()
    single_instance.listen(lambda: bg.ui_call(app, app.shell.show_window))
    try:
        app.mainloop()
    finally:
        single_instance.stop()
        app.shell.shutdown()  # трей і потік гарячих клавіш (повторний виклик — без ефекту)
        sensors.stop()


if __name__ == "__main__":
    main()
