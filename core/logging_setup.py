"""Спільне логування помилок фонових потоків у logs.txt у корені проєкту."""

import logging
import logging.handlers
import os
from core import paths as _paths

LOG_PATH = _paths.user_file("logs.txt")

_configured_loggers = set()


_shared_handler = None


def _handler() -> logging.Handler:
    """Один спільний обертовий файл-обробник на всі логери: logs.txt не росте безмежно
    (1 МБ × 3 копії), а ротація не конфліктує з іншими відкритими обробниками."""
    global _shared_handler
    if _shared_handler is None:
        _shared_handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8", delay=True)
        _shared_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    return _shared_handler


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if name not in _configured_loggers:
        logger.addHandler(_handler())
        # INFO: зміни системи (твіки, служби, плани живлення, автозапуск) мають лишати слід у logs.txt
        logger.setLevel(logging.INFO)
        logger.propagate = False
        _configured_loggers.add(name)
    return logger


def get_audit_logger() -> logging.Logger:
    """Журнал дій, що змінюють систему: кожне завершення процесу й кожне видалення
    (з причиною — яка кнопка) пишеться в logs.txt рівнем INFO."""
    logger = get_logger("lagnix.audit")
    logger.setLevel(logging.INFO)
    return logger
