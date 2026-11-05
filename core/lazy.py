# -*- coding: utf-8 -*-
"""Ленивый импорт: тяжёлая библиотека загружается при первом обращении, а не при запуске."""
import importlib


class Lazy:
    def __init__(self, name):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_mod", None)

    def _load(self):
        m = object.__getattribute__(self, "_mod")
        if m is None:
            m = importlib.import_module(object.__getattribute__(self, "_name"))
            object.__setattr__(self, "_mod", m)
        return m

    def __getattr__(self, attr):
        return getattr(self._load(), attr)
