"""Список вибору мови: Dropdown з назвами мов рідною мовою, кожна — своїм шрифтом
(щоб ієрогліфи не ставали квадратиками)."""

from __future__ import annotations

import customtkinter as ctk

from core import i18n
from ui.widgets.dropdown import Dropdown

_WIDTH = 240


def _font(code: str, size: int = 13) -> ctk.CTkFont:
    return ctk.CTkFont(family=i18n.ui_font_family(code), size=size)


class LanguagePicker(Dropdown):
    def __init__(self, master, current: str, on_select, width: int = _WIDTH):
        self._code_by_name = {name: code for code, name in i18n.LANGUAGES}
        self._font_by_name = {name: _font(code) for code, name in i18n.LANGUAGES}
        super().__init__(
            master, values=[name for _code, name in i18n.LANGUAGES],
            command=lambda name: on_select(self._code_by_name[name]), width=width, height=32,
            item_font=lambda name: self._font_by_name.get(name) or _font("en"),
            value=i18n.language_name(current),
        )
