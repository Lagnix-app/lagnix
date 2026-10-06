"""Імітація вікна деінсталятора із сумним роботом — лише для промо-відео (нічого не видаляє).

Малюється тими самими віджетами, що й решта Lagnix: RobotView (настрої sad/happy), кнопки з теми.
Кнопки нічого не роблять самі — натискання «імітує» сценарій (ui/promo/scenarios.py)."""

from __future__ import annotations

import math

import customtkinter as ctk

from core.i18n import t
from ui import theme
from ui.widgets import robot as robot_view

WIDTH, HEIGHT = 760, 620
ROBOT_SIZE = 250
STAGE_W, STAGE_H = 560, 340       # сцена робота: тут же літають зірочки
_STAR_COLORS = (theme.ACCENT_GREEN, theme.ACCENT_BLUE, "#ffd24d", "#ffffff")


def _mix(a: str, b: str, k: float) -> str:
    """Кольори a -> b (hex), k 0..1."""
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * k):02x}" for x, y in zip(pa, pb))


class UninstallWindow(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=theme.BG_MAIN)
        self.title("Lagnix")
        self.geometry(f"{WIDTH}x{HEIGHT}+60+20")
        self.resizable(False, False)

        header = ctk.CTkFrame(self, fg_color=theme.BG_PANEL, corner_radius=0, height=52)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(header, text=t("promo.uninstall.title"), font=theme.font_header(),
                     text_color=theme.TEXT_MAIN).pack(side="left", padx=22)
        ctk.CTkFrame(self, fg_color=theme.BORDER, height=1, corner_radius=0).pack(fill="x")

        self.stage = ctk.CTkFrame(self, fg_color=theme.BG_MAIN, corner_radius=0, width=STAGE_W, height=STAGE_H)
        self.stage.pack(pady=(26, 0))
        self.stage.pack_propagate(False)
        self.stars = ctk.CTkCanvas(self.stage, width=STAGE_W, height=STAGE_H, bg=theme.BG_MAIN,
                                   highlightthickness=0, bd=0)
        self.stars.place(x=0, y=0)
        self.robot = robot_view.RobotView(self.stage, size=ROBOT_SIZE, mood=robot_view.SAD, bg=theme.BG_MAIN)
        self._robot_base_y = (STAGE_H - ROBOT_SIZE) // 2 + 12
        self.robot.place(relx=0.5, y=self._robot_base_y, anchor="n")
        self._star_items: list[dict] = []

        self.question = ctk.CTkLabel(self, text=t("promo.uninstall.question"),
                                     font=ctk.CTkFont(family=theme.font_family(), size=26, weight="bold"),
                                     text_color=theme.TEXT_MAIN)
        self.question.pack(pady=(6, 22))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack()
        self.yes_button = ctk.CTkButton(
            row, text=t("promo.uninstall.yes"), width=250, height=56, corner_radius=12,
            font=ctk.CTkFont(family=theme.font_family(), size=19, weight="bold"),
            fg_color="#a8283f", hover_color=theme.ERROR, text_color="#ffffff", command=lambda: None)
        self.yes_button.pack(side="left", padx=10)
        self.no_button = ctk.CTkButton(
            row, text=t("promo.uninstall.no"), width=250, height=56, corner_radius=12,
            font=ctk.CTkFont(family=theme.font_family(), size=19, weight="bold"),
            fg_color=theme.ACCENT_GREEN, hover_color=theme.ACCENT_GREEN_DIM, text_color=theme.BG_MAIN,
            command=lambda: None)
        self.no_button.pack(side="left", padx=10)

    # ---------------------------------------------------------------- анімації

    def set_mood(self, mood: str) -> None:
        self.robot.set_mood(mood)

    def set_hop(self, lift_px: float) -> None:
        """Підйом робота над базовою лінією (px, 0 — стоїть)."""
        self.robot.place_configure(y=round(self._robot_base_y - lift_px))

    def burst_stars(self, t_since: float) -> None:
        """Зірочки розлітаються від робота й згасають: t_since — секунди від початку радості."""
        if not self._star_items:
            cx, cy = STAGE_W / 2, self._robot_base_y + ROBOT_SIZE * 0.5
            for i in range(10):
                angle = math.radians(-100 + i * 38 + (i % 2) * 11)
                self._star_items.append({
                    "id": self.stars.create_polygon(0, 0, 0, 0, 0, 0, state="hidden", outline=""),
                    "angle": angle, "speed": 90 + (i % 3) * 38, "size": 11 + (i % 4) * 4,
                    "delay": i * 0.045, "color": _STAR_COLORS[i % len(_STAR_COLORS)], "spin": 1 + i % 3,
                    "origin": (cx, cy)})
        for s in self._star_items:
            age = t_since - s["delay"]
            if age < 0 or age > 1.5:
                self.stars.itemconfigure(s["id"], state="hidden")
                continue
            k = age / 1.5
            dist = 100 + s["speed"] * (1 - (1 - k) ** 2) * 1.35
            x = s["origin"][0] + math.cos(s["angle"]) * dist * 1.25
            y = s["origin"][1] + math.sin(s["angle"]) * dist * 0.85
            size = s["size"] * (1 - 0.35 * k) * min(1.0, age * 8)
            rot = age * s["spin"] * 2.4
            pts = []
            for j in range(10):
                r = size if j % 2 == 0 else size * 0.45
                a = rot + j * math.pi / 5 - math.pi / 2
                pts += [x + math.cos(a) * r, y + math.sin(a) * r]
            fill = _mix(s["color"], theme.BG_MAIN, max(0.0, (k - 0.55) / 0.45))
            self.stars.coords(s["id"], *pts)
            self.stars.itemconfigure(s["id"], fill=fill, state="normal")
