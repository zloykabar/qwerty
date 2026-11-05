# -*- coding: utf-8 -*-
"""Пути и JSON-конфиги. Ресурсы ищутся относительно файлов (или sys._MEIPASS в сборке PyInstaller)."""
import json
import os
import sys

FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES_DIR = getattr(sys, "_MEIPASS", BASE_DIR)


def user_path(*parts):
    """Папка с данными пользователя (рядом с программой): конфиги, логи, профиль браузера."""
    return os.path.join(BASE_DIR, *parts)


def resource_path(*parts):
    """Встроенные ресурсы (в сборке PyInstaller лежат в sys._MEIPASS)."""
    return os.path.join(RES_DIR, *parts)


def icon_path():
    for p in (user_path("assets", "app.ico"), resource_path("assets", "app.ico")):
        if os.path.isfile(p):
            return p
    return ""


def load_json(rel, default=None, kind="config"):
    """Читает config/<rel> (сначала пользовательский, потом встроенный). При ошибке возвращает default."""
    for base in (user_path, resource_path):
        p = base(kind, rel)
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8-sig") as f:
                    return json.load(f)
            except Exception:
                from core.logsetup import get
                get("paths").exception("Не удалось прочитать %s", p)
    return default


def save_json(rel, data, kind="config"):
    p = user_path(kind, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
