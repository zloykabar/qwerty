# -*- coding: utf-8 -*-
"""
Ставит Whisper: очень точное распознавание речи (в том числе тихой и шёпотом).
Запуск: Install_Whisper.bat. Использует ту же папку xtts_env (Python 3.11), что и клон голоса.
На видеокарте NVIDIA ставится модель large-v3-turbo (около 1,6 ГБ), на процессоре small (около 1 ГБ).
"""
import os
import sys

import install_xtts as ix

MARK = os.path.join(ix.ENV, "whisper.ok")


def main():
    if os.path.exists(MARK):
        os.remove(MARK)
    print("1/4. Окружение...")
    if ix.ensure_env() != 0:
        return 1
    print("2/4. Ставлю быстрый распознаватель (faster-whisper)...")
    if ix.run([ix.PY, "-m", "pip", "install", "faster-whisper", "numpy"]) != 0:
        return 1
    print("3/4. Ставлю запасной движок (transformers), если понадобится...")
    ix.run([ix.PY, "-m", "pip", "install", "transformers"])
    print("4/4. Скачиваю модель распознавания, подождите...")
    env = dict(os.environ, HF_HUB_DISABLE_SYMLINKS_WARNING="1")
    if ix.run([ix.PY, os.path.join(ix.BASE, "whisper_worker.py"), "auto", "--download"], env=env) != 0:
        print("Не получилось скачать или загрузить модель. Покажите это окно разработчику.")
        return 1
    with open(MARK, "w") as f:
        f.write("ok")
    print("\nГотово! Перезапустите Джарвиса: он сам переключится на Whisper.")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception as e:
        print("Ошибка:", e)
    sys.exit(code)
