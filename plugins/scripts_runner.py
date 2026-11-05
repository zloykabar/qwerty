# -*- coding: utf-8 -*-
"""
Запуск ваших собственных скриптов из папки scripts.
Положите туда .py / .bat / .ps1 / .exe и скажите: «Джарвис, запусти скрипт <название>».
Название сравнивается с именем файла без расширения (латиница и русская транслитерация подходят).
"""
import os
import subprocess
import sys

NAME = "Запуск скриптов"
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
os.makedirs(SCRIPTS_DIR, exist_ok=True)
EXTS = (".py", ".bat", ".cmd", ".ps1", ".exe")
NO_WINDOW = 0x08000000


def _scripts():
    return {os.path.splitext(f)[0].lower(): f for f in os.listdir(SCRIPTS_DIR) if f.lower().endswith(EXTS)}


def run_script(args, jarvis):
    scripts = _scripts()
    if not scripts:
        return "Папка scripts пока пуста. Положите туда свои скрипты."
    name = jarvis.best_match(args, list(scripts))
    if not name:
        return "Не нашёл такой скрипт. Есть: " + ", ".join(scripts)
    path = os.path.join(SCRIPTS_DIR, scripts[name])
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        subprocess.Popen([sys.executable, path], cwd=SCRIPTS_DIR, creationflags=NO_WINDOW)
    elif ext == ".ps1":
        subprocess.Popen(["powershell", "-ExecutionPolicy", "Bypass", "-File", path],
                         cwd=SCRIPTS_DIR, creationflags=NO_WINDOW)
    else:
        os.startfile(path)
    return "Запускаю скрипт " + name


def list_scripts(args, jarvis):
    scripts = _scripts()
    return ("Доступные скрипты: " + ", ".join(scripts)) if scripts else "Скриптов пока нет."


COMMANDS = [
    {"triggers": ["запусти скрипт", "выполни скрипт"], "run": run_script},
    {"triggers": ["какие скрипты", "список скриптов"], "run": list_scripts},
]
