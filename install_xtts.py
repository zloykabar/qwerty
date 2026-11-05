# -*- coding: utf-8 -*-
"""
Ставит XTTS (клонирование голоса) для Джарвиса. Запуск: Install_XTTS.bat.
XTTS и Whisper нужен Python 3.11: он скачивается портативно в папку xtts_env, система не трогается. Нужно около 3-4 ГБ и интернет.
"""
import os
import subprocess
import sys
import urllib.request
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(BASE, "xtts_env")
PY = os.path.join(ENV, "Scripts", "python.exe")
VOICES = os.path.join(BASE, "voices_xtts")
MARK = os.path.join(ENV, "installed.ok")


def run(cmd, **kw):
    print("> " + " ".join(cmd))
    return subprocess.call(cmd, **kw)


def has_nvidia():
    try:
        return subprocess.run(["nvidia-smi"], capture_output=True).returncode == 0
    except Exception:
        return False


PY311_URL = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
GETPIP_URL = "https://bootstrap.pypa.io/get-pip.py"


def _download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Jarvis-installer"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)


def ensure_env():
    """Ставит отдельный портативный Python 3.11 в xtts_env\\Scripts (общий для клона голоса и Whisper).
    Ничего устанавливать в систему не нужно."""
    scripts = os.path.dirname(PY)
    if os.path.exists(PY) and subprocess.call([PY, "-m", "pip", "--version"],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
        print("xtts_env уже есть.")
        return 0
    try:
        os.makedirs(scripts, exist_ok=True)
        if not os.path.exists(PY):
            print("Скачиваю портативный Python 3.11...")
            zpath = os.path.join(ENV, "py311.zip")
            _download(PY311_URL, zpath)
            with zipfile.ZipFile(zpath) as z:
                z.extractall(scripts)
            os.remove(zpath)
            # включаем site-packages и добавляем папку Джарвиса (..\\..) в пути поиска модулей
            with open(os.path.join(scripts, "python311._pth"), "w", encoding="ascii") as f:
                f.write("python311.zip\n.\n..\\..\nimport site\n")
        gp = os.path.join(ENV, "get-pip.py")
        _download(GETPIP_URL, gp)
        code = run([PY, gp, "--no-warn-script-location"])
        os.remove(gp)
        return code
    except Exception as e:
        print("Не получилось подготовить Python 3.11:", e)
        return 1


def install_torch():
    run([PY, "-m", "pip", "install", "--upgrade", "pip"])
    # transformers требует PyTorch 2.6 или новее, на более старом нейросети не стартуют
    if has_nvidia():
        print("   Найдена видеокарта NVIDIA - ставлю версию с CUDA.")
        cmd = [PY, "-m", "pip", "install", "--upgrade", "torch==2.6.0", "torchaudio==2.6.0",
               "--index-url", "https://download.pytorch.org/whl/cu124"]
    else:
        print("   Видеокарта NVIDIA не найдена - будет работать на процессоре (медленнее).")
        cmd = [PY, "-m", "pip", "install", "--upgrade", "torch>=2.6,<2.9", "torchaudio>=2.6,<2.9"]
    return run(cmd)


def main():
    os.makedirs(VOICES, exist_ok=True)
    if os.path.exists(MARK):
        os.remove(MARK)
    print("1/4. Окружение...")
    if ensure_env() != 0:
        return 1

    print("2/4. Ставлю PyTorch (самая долгая часть)...")
    if install_torch() != 0:
        return 1

    print("3/4. Ставлю XTTS...")
    if run([PY, "-m", "pip", "install", "coqui-tts"]) != 0:
        return 1

    print("4/4. Скачиваю модель голоса (около 2 ГБ), подождите...")
    env = dict(os.environ, COQUI_TOS_AGREED="1")
    if run([PY, os.path.join(BASE, "xtts_worker.py"), "--download"], env=env) != 0:
        print("Не получилось скачать или загрузить модель. Покажите это окно разработчику.")
        return 1

    with open(MARK, "w") as f:
        f.write("ok")
    print("\nГотово!")
    print("Осталось положить образец голоса в папку voices_xtts: файл .wav, 10-30 секунд")
    print("чистой речи без музыки и шума. Используйте свой голос или голос человека,")
    print("который разрешил его использовать. Потом перезапустите Джарвиса.")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception as e:
        print("Ошибка:", e)
    sys.exit(code)
