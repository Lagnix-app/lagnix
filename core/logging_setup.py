"""Спільне логування помилок фонових потоків у logs.txt у корені проєкту."""

import logging
import os

LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs.txt")

_configured_loggers = set()


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if name not in _configured_loggers:
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
        logger.propagate = False
        _configured_loggers.add(name)
    return logger
