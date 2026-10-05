"""«Мову буде змінено після перезапуску Lagnix» — модальне вікно (ui/widgets/modal.py)
НОВОЮ мовою (не поточною) з кнопками «Перезапустити зараз» / «Пізніше»."""

from core import i18n
from core.i18n import t
from ui.widgets import modal


def ask_restart(master, language: str) -> bool:
    """True — «Перезапустити зараз»; False — «Пізніше» (також Esc і клік по затемненню)."""
    family = i18n.ui_font_family(language)  # шрифт НОВОЇ мови (CJK без «квадратиків»)
    with i18n.using_language(language):
        title, text = t("restart.title"), t("restart.text")
        now_text, later_text = t("restart.now"), t("restart.later")
    window = modal.Modal(master, title, text, cancel_value=False, font_family=family)
    window.add_button(later_text, False, "secondary")
    window.add_button(now_text, True, "primary")
    return bool(window.run())
