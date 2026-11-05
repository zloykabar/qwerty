# -*- coding: utf-8 -*-
"""
Скачивает живой нейросетевой голос для Джарвиса (Piper) прямо в эту папку:
  piper\\piper.exe        - программа озвучки
  voices\\*.onnx          - русские голоса (мужские: Руслан, Денис, Дмитрий)
Запуск: двойной щелчок по INSTALL_JARVIS.bat. Нужен интернет, около 250 МБ.
"""
import os
import sys
import urllib.error
import urllib.request
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
PIPER_URL = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip"
VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/{n}/{q}/ru_RU-{n}-{q}.onnx"
VOICES = ["ruslan", "denis", "dmitri"]          # добавьте "irina" для женского голоса
QUALITIES = ["medium", "high", "low"]


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Jarvis-installer"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest + ".part", "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, -1
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                if pct != last and pct % 10 == 0:
                    last = pct
                    print(f"   {pct}%", flush=True)
    os.replace(dest + ".part", dest)


def main():
    piper_dir = os.path.join(BASE, "piper")
    voices_dir = os.path.join(BASE, "voices")
    os.makedirs(voices_dir, exist_ok=True)

    if os.path.exists(os.path.join(piper_dir, "piper.exe")):
        print("Piper уже установлен, пропускаю.")
    else:
        print("1/2. Скачиваю Piper (программа озвучки)...")
        zpath = os.path.join(BASE, "piper_windows_amd64.zip")
        download(PIPER_URL, zpath)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(BASE)
        os.remove(zpath)
        if not os.path.exists(os.path.join(piper_dir, "piper.exe")):
            print("Не нашёл piper.exe после распаковки. Покажите это окно разработчику.")
            return 1

    print("2/2. Скачиваю русские голоса...")
    got = 0
    for name in VOICES:
        if any(f.startswith(f"ru_RU-{name}-") and f.endswith(".onnx") for f in os.listdir(voices_dir)):
            print(f" - {name}: уже есть")
            got += 1
            continue
        for q in QUALITIES:
            model = os.path.join(voices_dir, f"ru_RU-{name}-{q}.onnx")
            try:
                print(f" - {name} ({q})...")
                download(VOICE_URL.format(n=name, q=q), model)
                download(VOICE_URL.format(n=name, q=q) + ".json", model + ".json")
                got += 1
                break
            except urllib.error.HTTPError as e:
                for p in (model, model + ".json", model + ".part", model + ".json.part"):
                    if os.path.exists(p):
                        os.remove(p)
                if e.code != 404:
                    print(f"   ошибка {e.code}, пробую дальше")
            except Exception as e:
                print(f"   не получилось: {e}")
                break
    if not got:
        print("\nГолоса не скачались. Проверьте интернет и запустите установку ещё раз.")
        return 1
    print("\nГотово! Перезапустите Джарвиса и выберите голос на вкладке «Голос».")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception as e:
        print("Ошибка:", e)
    sys.exit(code)
