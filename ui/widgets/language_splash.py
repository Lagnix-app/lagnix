"""Заставка на час зміни мови: темне вікно поверх усього вікна Lagnix, робот і текст
новою мовою. Під нею вкладки перебудовуються без видимого «ламання»; наприкінці — плавне
зникнення (~200 мс, прозорість окремого вікна)."""

import customtkinter as ctk

from core.i18n import t
from ui import theme
from ui.widgets import robot as robot_view

_FADE_MS = 200
_FRAME_MS = 16


class LanguageSplash(ctk.CTkToplevel):
    def __init__(self, root):
        super().__init__(root)
        self._fading = False
        self.withdraw()
        self.overrideredirect(True)
        self.configure(fg_color=theme.BG_MAIN)
        self.attributes("-topmost", True)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.place(relx=0.5, rely=0.5, anchor="center")
        self.robot = robot_view.RobotView(body, size=140, mood=robot_view.CALM, bg=theme.BG_MAIN)
        self.robot.pack()
        self.label = ctk.CTkLabel(body, text=t("language.changing"), font=theme.font_header(),
                                  text_color=theme.TEXT_MAIN)
        self.label.pack(pady=(10, 0))

        root.update_idletasks()
        self.geometry(f"{root.winfo_width()}x{root.winfo_height()}+{root.winfo_rootx()}+{root.winfo_rooty()}")
        self.attributes("-alpha", 1.0)
        self.deiconify()
        self.robot.set_running(True)
        self.update()  # показати до важкої роботи

    def fade_out(self) -> None:
        if self._fading:
            return
        self._fading = True
        self._step(0)

    def _step(self, elapsed_ms: int) -> None:
        try:
            if elapsed_ms >= _FADE_MS:
                self.destroy()
                return
            self.attributes("-alpha", 1.0 - theme.ease_out_cubic(elapsed_ms / _FADE_MS))
            self.after(_FRAME_MS, self._step, elapsed_ms + _FRAME_MS)
        except Exception:  # вікно вже знищено (вихід із програми посеред зникання)
            pass
