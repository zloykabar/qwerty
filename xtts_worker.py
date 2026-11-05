# -*- coding: utf-8 -*-
"""
Фоновая программа озвучки XTTS. Запускается Джарвисом внутри папки xtts_env (Python 3.11),
сама по себе её запускать не нужно.
Обмен: Джарвис пишет в stdin строки JSON {"text", "wav", "speed"},
в ответ приходят кадры: 4 байта длины + данные. Первый кадр - JSON "готово",
дальше - звук (16 бит, моно). Пустой кадр означает ошибку.
"""
import json
import os
import struct
import sys
import traceback

os.environ.setdefault("COQUI_TOS_AGREED", "1")
MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"

REAL_OUT = sys.stdout.buffer
sys.stdout = sys.stderr          # всё, что библиотеки напечатают, уходит в лог, а не в канал со звуком


def log(msg):
    print("[xtts] " + msg, file=sys.stderr, flush=True)


def send(data):
    REAL_OUT.write(struct.pack("<I", len(data)))
    if data:
        REAL_OUT.write(data)
    REAL_OUT.flush()


def load():
    import torch
    from TTS.api import TTS
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    log("загружаю модель на " + dev)
    tts = TTS(MODEL).to(dev)
    return tts, dev


_cache = {}


def synth(tts, text, wav, speed):
    import numpy as np
    arr = None
    try:
        model = tts.synthesizer.tts_model
        if wav not in _cache:
            _cache[wav] = model.get_conditioning_latents(audio_path=[wav])
        gpt, spk = _cache[wav]
        out = model.inference(text, "ru", gpt, spk, temperature=0.65, speed=speed)
        arr = out["wav"]
    except Exception:
        traceback.print_exc()
        arr = tts.tts(text=text, speaker_wav=wav, language="ru", speed=speed)
    a = np.clip(np.asarray(arr, dtype=np.float32), -1.0, 1.0)
    return (a * 32767).astype("<i2").tobytes()


def main():
    tts, dev = load()
    if "--download" in sys.argv:
        log("модель скачана и загружается нормально")
        return
    send(json.dumps({"ready": True, "sr": 24000, "device": dev}).encode("utf-8"))
    stdin = sys.stdin.buffer
    while True:
        line = stdin.readline()
        if not line:
            break                       # Джарвис закрылся - закрываемся и мы
        try:
            req = json.loads(line.decode("utf-8"))
            send(synth(tts, req["text"], req["wav"], float(req.get("speed", 1.0))))
        except Exception:
            traceback.print_exc()
            send(b"")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
