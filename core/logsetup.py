# -*- coding: utf-8 -*-
"""Логи: logs/assistant.log с ротацией. Пишем распознанную речь, команды, шаги, ошибки. Звук НЕ сохраняется."""
import logging
import os
from logging.handlers import RotatingFileHandler

_done = False


def setup():
    global _done
    if _done:
        return
    _done = True
    from core.paths import user_path
    d = user_path("logs")
    try:
        os.makedirs(d, exist_ok=True)
        h = RotatingFileHandler(os.path.join(d, "assistant.log"), maxBytes=1_000_000, backupCount=5,
                                encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger("jarvis")
        root.setLevel(logging.DEBUG)
        root.addHandler(h)
        root.propagate = False
    except Exception:
        pass


def get(name):
    setup()
    return logging.getLogger("jarvis." + name)
