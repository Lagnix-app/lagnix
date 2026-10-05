"""Перевірка та підвищення прав адміністратора Lagnix."""

import ctypes
import os
import sys

from core.logging_setup import get_logger
from core.i18n import t, ui_font_family


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch_as_admin() -> bool:
    """Просить Windows перезапустити поточний процес з правами адміністратора.

    Викликає ShellExecuteW з дієсловом "runas" — Windows сама показує
    стандартне вікно UAC. Повертає True, якщо запит вдалося передати системі
    (вікно UAC з'явиться); викликач після цього має завершити поточний
    екземпляр. False повертається лише при системній помилці запуску
    (напр. файл не знайдено) — відмову користувача у вікні UAC
    ShellExecuteW не повідомляє.
    """
    try:
        exe = sys.executable
        if getattr(sys, "frozen", False):
            args = sys.argv[1:]
        else:
            args = [os.path.abspath(sys.argv[0]), *sys.argv[1:]]
        params = " ".join(f'"{arg}"' for arg in args)

        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        if result <= 32:
            # 5 = SE_ERR_ACCESSDENIED: користувач відмовив у вікні UAC.
            get_logger("core.admin").error("ShellExecuteW runas returned %s", result)
        return result > 32
    except (AttributeError, OSError):
        get_logger("core.admin").exception("Failed to request administrator rights")
        return False


def ask_retry_admin() -> bool:
    """Вікно після відмови в UAC. True — «Спробувати ще раз», False — «Вийти»."""
    import tkinter as tk

    result = {"retry": False}
    root = tk.Tk()
    root.title("Lagnix")
    root.resizable(False, False)
    root.configure(bg="#0d1321", padx=24, pady=20)
    tk.Label(
        root, text=t("admin.need_rights"),
        bg="#0d1321", fg="#e8ecf5", font=(ui_font_family(), 11), justify="center",
    ).pack(pady=(0, 16))
    row = tk.Frame(root, bg="#0d1321")
    row.pack()

    def choose(retry: bool):
        result["retry"] = retry
        root.destroy()

    tk.Button(row, text=t("admin.retry"), width=18, command=lambda: choose(True),
              bg="#2ee59d", fg="#0d1321", relief="flat").pack(side="left", padx=6)
    tk.Button(row, text=t("admin.exit"), width=12, command=lambda: choose(False),
              bg="#2d3953", fg="#e8ecf5", relief="flat").pack(side="left", padx=6)
    root.protocol("WM_DELETE_WINDOW", lambda: choose(False))
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 3
    root.geometry(f"+{x}+{y}")
    root.mainloop()
    return result["retry"]
