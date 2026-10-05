"""Вікно «Ці програми не закрилися»: після м'якого закриття Ігрового режиму список програм, що
лишились відкритими (показали своє вікно підтвердження — «Закрити всі вкладки?», «Вийти з
зустрічі?»). Для кожної — «Закрити примусово» / «Залишити» і галочка «Завжди закривати
примусово»; внизу — «Закрити всі примусово» / «Залишити всі». Модальне вікно всередині
головного (ui/widgets/modal.py). Саме закриття виконує core (process_control), не це вікно."""

import customtkinter as ctk

from core.i18n import t
from ui import theme
from ui.widgets import modal


class _Row:
    def __init__(self, window: "modal.Modal", app: dict, on_decide):
        self.app = app
        self.frame = theme.plain_frame(window.body)
        self.frame.pack(fill="x", pady=(0, 14))
        head = theme.plain_frame(self.frame)
        head.pack(fill="x")
        ctk.CTkLabel(head, text=app["title"], font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(side="left")
        ctk.CTkLabel(head, text=f"  {t('units.gb_1', v=app['memory_mb'] / 1024) if app['memory_mb'] >= 1024 else t('units.mb_0', mb=app['memory_mb'])}",
                     font=theme.font_small(), text_color=theme.TEXT_DIM).pack(side="left")
        wrap = window.wrap_dp
        ctk.CTkLabel(self.frame, text=app["reason"], font=theme.font_small(), text_color=theme.TEXT_DIM, anchor="w",
                     justify="left", wraplength=wrap).pack(fill="x")
        note, color = self._note(app)
        if note:
            ctk.CTkLabel(self.frame, text=note, font=theme.font_small(), text_color=color, anchor="w",
                         justify="left", wraplength=wrap).pack(fill="x", pady=(2, 0))

        self.actions = theme.plain_frame(self.frame)
        self.actions.pack(fill="x", pady=(8, 0))
        self.force_button = ctk.CTkButton(
            self.actions, text=t("game_mode.force.close"), height=28, corner_radius=8, width=10,
            fg_color="#a8283f", hover_color=theme.ERROR, text_color="#ffffff", font=theme.font_small(),
            command=lambda: on_decide(self, True))
        self.force_button.pack(side="left")
        ctk.CTkButton(
            self.actions, text=t("game_mode.force.keep"), height=28, corner_radius=8, width=10, font=theme.font_small(),
            fg_color="transparent", border_width=1, border_color=theme.BORDER, hover_color=theme.BG_PANEL_LIGHT,
            text_color=theme.TEXT_MAIN, command=lambda: on_decide(self, False)).pack(side="left", padx=(8, 0))
        self.always = ctk.CTkCheckBox(self.actions, text=t("game_mode.force.always"), font=theme.font_small(),
                                      checkbox_width=18, checkbox_height=18)
        self.always.pack(side="right")
        if app.get("never_force"):
            self.always.configure(state="disabled")  # відеозв'язок — лише вручну, без «завжди»
        self.result_label = ctk.CTkLabel(self.frame, text="", font=theme.font_small(), anchor="w")
        self.decision: bool | None = None

    @staticmethod
    def _note(app: dict) -> tuple[str, str]:
        if app.get("never_force"):
            return t("game_mode.force.call_only"), theme.ACCENT_BLUE
        if app.get("category") == "browser":
            restore = app.get("session_restore")
            if restore is True:
                return t("game_mode.force.browser_restore"), theme.TEXT_DIM
            return (t("game_mode.force.browser_no_restore") if restore is False
                    else t("game_mode.force.browser_unknown")), theme.WARNING
        return "", theme.TEXT_DIM

    def set_decision(self, force: bool) -> None:
        self.decision = force
        self.actions.pack_forget()
        self.result_label.configure(text=t("game_mode.force.will_close") if force else t("game_mode.force.kept"),
                                    text_color=theme.ERROR if force else theme.TEXT_DIM)
        self.result_label.pack(fill="x", pady=(8, 0))

    def remember(self) -> bool:
        return bool(self.decision and not self.app.get("never_force") and self.always.get())


def ask(parent, kept: list[dict]) -> tuple[list[str], list[str]]:
    """Показує вікно й чекає. -> (ключі програм для примусового закриття, ключі з галочкою
    «Завжди закривати примусово»). Esc / клік по затемненню = «Залишити всі», що лишились."""
    window = modal.Modal(parent, t("game_mode.force.title"), t("game_mode.force.text"),
                         warning=t("game_mode.force.warning"), kind="warning", cancel_value=None)
    rows: list[_Row] = []

    def decide(row: _Row, force: bool) -> None:
        if row.decision is not None:
            return
        row.set_decision(force)
        if all(r.decision is not None for r in rows):
            window.close(True)

    def decide_rest(force: bool) -> None:
        for row in rows:
            if row.decision is None:
                row.set_decision(force)
        window.close(True)

    for app in kept:
        rows.append(_Row(window, app, decide))
    window.add_button(t("game_mode.force.keep_all"), style="secondary", command=lambda: decide_rest(False))
    window.add_button(t("game_mode.force.close_all"), style="danger", command=lambda: decide_rest(True))
    window.run()
    chosen = [r.app["key"] for r in rows if r.decision]
    remember = [r.app["key"] for r in rows if r.remember()]
    return chosen, remember
