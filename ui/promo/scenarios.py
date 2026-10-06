"""Сценарії промо-відео: що відбувається у вікні й коли (час — секунди від початку запису).

Кожен сценарій будує вікно й повертає (root, scenario(player), layout). Усе — імітація:
сумний робот-деінсталятор нічого не видаляє, «закриття програм» міняє лише вигаданий список процесів
(ui/promo/fakes.py), справжні процеси, файли, твіки й план живлення не чіпаються."""

from __future__ import annotations

import math

import customtkinter as ctk

from core.i18n import t
from ui import theme
from ui.widgets import robot as robot_view


_FAKE_GAMES = [
    {"key": "riot:valorant", "name": "VALORANT", "platform": "Riot", "folder": "", "exe": "valorant.exe",
     "exe_names": ["valorant.exe"], "kind": "game"},
    {"key": "steam:730", "name": "Counter-Strike 2", "platform": "Steam", "folder": "", "exe": "cs2.exe",
     "exe_names": ["cs2.exe"], "kind": "game"},
    {"key": "steam:570", "name": "Dota 2", "platform": "Steam", "folder": "", "exe": "dota2.exe",
     "exe_names": ["dota2.exe"], "kind": "game"},
    {"key": "epic:fortnite", "name": "Fortnite", "platform": "Epic", "folder": "", "exe": "fortniteclient.exe",
     "exe_names": ["fortniteclient.exe"], "kind": "game"},
]


def _ease_out(k: float) -> float:
    return 1 - (1 - max(0.0, min(k, 1.0))) ** 3


# ============================================================ відео 1: Sad robot

def build_sad_robot(lang: str):
    from ui.promo.uninstall_window import HEIGHT, WIDTH, UninstallWindow
    win = UninstallWindow()
    state = {"happy_at": None}

    def scenario(p):
        yield 0.1
        p.place_cursor((WIDTH + 90, HEIGHT - 70))   # за межами кадру: справа внизу
        p.caption("uninstall_title", 2.1)
        p.cue("window_in")
        yield 2.2
        # курсор з'являється й повільно їде до «Yes, remove»
        p.show_cursor(True)
        yield p.move(p.center(win.yes_button, dx=-10, dy=2), 3.9, bend=-50)
        p.cue("at_yes")
        p.hover(win.yes_button, True)
        yield 0.35
        p.caption("dots", 1.9)
        yield p.shake(1.7, amp=3.2)
        yield 0.25
        # різкий ривок до «No, I'll stay» і клік
        p.hover(win.yes_button, False)
        p.cue("jerk")
        yield p.move(p.center(win.no_button), 0.2, bend=18)
        p.hover(win.no_button, True)
        yield 0.28
        p.click()
        win.set_mood(robot_view.HAPPY)
        state["happy_at"] = p.sched
        p.cue("happy")
        yield 3.2
        p.cue("outro")

    def ticker(now):
        started = state["happy_at"]
        if started is None:
            return
        # «now» — реальний час запису; на момент кліку сценарій фіксує p.sched ≈ now
        age = now - state.setdefault("happy_real", now)
        hop = 0.0
        for n, (start, height, dur) in enumerate(((0.0, 46, 0.5), (0.5, 24, 0.38))):
            if start <= age < start + dur:
                hop = height * math.sin(math.pi * (age - start) / dur)
        win.set_hop(hop)
        win.burst_stars(age)

    layout = {"mode": "dialog", "scale": 1.28}
    return win, scenario, layout, ticker


# ============================================================ відео 2: One click

def build_one_click(lang: str):
    from core import app_catalog as catalog
    from core import smart_apps
    from ui import game_mode_tab as gm_mod
    from ui import monitor_tab as mon_mod
    from ui.main_window import MainWindow
    from ui.promo import fakes
    from ui.widgets import modal

    world = fakes.World()
    fakes.install(world)
    mon_mod.MonitorTab._start_worker = lambda self: None          # дані подає сценарій, не потік
    gm_mod.GameModeTab._start_workers = lambda self: None          # без сканування справжніх ігор/процесів

    app = MainWindow()
    app.geometry("1000x920+0+0")
    app.update()
    mon = app.tab_frames["monitor"]
    gm = app.tab_frames["game_mode"]
    state = {"modal": None, "closing": []}

    # ---- початкові дані: історія графіка + список програм Ігрового режиму
    for i in range(60):
        world.clock = -30 + i * 0.5
        s = world.snapshot()
        mon.graph.push(s["cpu_percent"], s["gpu"]["load_percent"], s["ram_percent"], s["cpu_temp"])
    world.clock = 0.0
    snap = world.snapshot()
    mon._visible = True
    mon._apply_snapshot(snap)
    groups = snap["process_groups"]
    running = smart_apps.scan_running(groups, None, [])
    mem = {}
    for g in groups:
        for m in g["members"]:
            mem[m["name"].lower()] = mem.get(m["name"].lower(), 0.0) + m["memory_mb"]
    gm._on_level(catalog.MAX)                    # у «Максимальному» рівні в списку й месенджери (Discord)
    gm._apply_preview(running, mem, False)
    gm._apply_games(_FAKE_GAMES)
    mon.process_table.toggle.set(t("monitor.sort_ram"))
    mon.process_table._on_toggle(t("monitor.sort_ram"))

    def request_enable(self):
        """Підміна справжнього «Увімкнути»: те саме вікно підтвердження, але без реального закриття."""
        apps = list(self._current_apps())
        names = [a["title"] for a in apps]
        shown = "\n".join("• " + n for n in names)
        box = modal.Modal(self, t("tabs.game_mode"), t("game_mode.confirm_close", shown=shown), kind="warning",
                          cancel_value=False)
        robot = robot_view.RobotView(box.body, size=96, mood=robot_view.GAMING, bg=theme.BG_PANEL)
        robot.pack(pady=(2, 0))
        box.add_button(t("common.cancel"), False, "secondary")
        state["close_button"] = box.add_button(t("common.close"), True, "primary")
        state["modal"], state["apps"] = box, apps
        self.after(350, box.show)      # вкладка має встигнути з'явитися під затемненням

    gm_mod.GameModeTab._request_enable = request_enable

    scroll = {"cur": 0.0, "target": 0.0, "at": 0.0}

    def scroll_to(px: float) -> None:
        scroll["target"] = px

    def feed(now):
        """Вигадані метрики ~3 рази на секунду (як живий Монітор) — графік і кільця рухаються;
        плюс плавна прокрутка сторінки Монітора до таблиці процесів."""
        dt = max(now - scroll["at"], 0.0)
        scroll["at"] = now
        if abs(scroll["target"] - scroll["cur"]) > 0.4:
            scroll["cur"] += (scroll["target"] - scroll["cur"]) * (1 - math.exp(-dt * 7.5))
            height = max(mon.page.inner.winfo_reqheight(), 1)
            mon.page.canvas.yview_moveto(scroll["cur"] / height)
        if now - state.get("fed", -9) < 0.33:
            return
        state["fed"] = now
        world.clock = now
        mon._apply_snapshot(world.snapshot())

    def scenario(p):
        yield 0.1
        mon._apply_snapshot(world.snapshot())
        p.place_cursor((1010, 640))
        p.caption("ram_full", 2.0)
        p.cue("window_in")
        yield 2.2
        button = mon.status_robot.action_button
        yield 0.5
        scroll_to(430)                         # показуємо, що «висить» у фоні: Chrome, Discord, Steam
        p.cue("processes")
        yield 2.1
        scroll_to(0)
        yield 0.7
        p.show_cursor(True)
        yield p.move(p.center(button), 1.1, bend=-40)
        p.hover(button, True)
        yield 0.3
        p.click()
        p.hover(button, False)
        button.invoke()                       # перехід у «Ігровий режим» і вікно підтвердження
        p.cue("game_tab")
        yield 1.35
        close = state["close_button"]
        yield p.move(p.center(close), 0.9, bend=30)
        p.hover(close, True)
        yield 0.3
        p.click()
        p.hover(close, False)
        close.invoke()
        yield 0.45
        # ---- імітація закриття: програми зникають по одній (пам'ять звільняється трохи згодом)
        gm._busy = True
        gm._render()
        for app_row in state["apps"]:
            gm._running_apps = [a for a in gm._running_apps if a["key"] != app_row["key"]]
            gm._render()
            state.setdefault("to_close", []).append(app_row["name"])
            yield 0.55
        closed = [{"title": a["title"], "name": a["name"], "memory_mb": a["memory_mb"], "exe_path": None}
                  for a in state["apps"]]
        gm.state.update(is_active=True, closed_apps=closed, freed_mb=sum(c["memory_mb"] for c in closed),
                        plan_name="Lagnix Ultra", plan_value=None)
        gm._busy = False
        gm._render()
        for exe in state["to_close"]:
            world.close_app(exe)
        p.cue("closed")
        yield 0.8
        nav = app.nav_buttons["monitor"]
        yield p.move(p.center(nav), 0.8, bend=25)
        p.hover(nav, True)
        yield 0.25
        p.click()
        p.hover(nav, False)
        app.select_tab("monitor")
        p.cue("monitor_again")
        p.caption("one_click", 3.0)
        yield 3.1
        p.caption("safe", 4.3)
        yield 4.4
        p.cue("outro")

    layout = {"mode": "app", "scale": 1.0}
    return app, scenario, layout, feed
