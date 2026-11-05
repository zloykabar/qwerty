# -*- coding: utf-8 -*-
"""
Фоновая программа распознавания речи. Запускается Джарвисом внутри xtts_env (Python 3.11).
Обмен: Джарвис присылает кадры (4 байта длины + звук 16 кГц, 16 бит, моно),
в ответ приходят кадры с JSON {"text": ...}. Первый кадр - JSON "готово". Пустой кадр от Джарвиса = выход.
Аргументы: [модель или auto] [--download]

Скорость: если установлен faster-whisper (CTranslate2), модель грузится за секунды и считает
на процессоре в разы быстрее. Если его нет - работаем на прежнем transformers (медленнее).

Смешанная речь ru-en: язык берётся из config.json ("asr_language"), а при неуверенности
модели делается второй короткий проход, из вариантов выбирается лучший.
"""
import json
import os
import struct
import sys
import time
import traceback

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

REAL_OUT = sys.stdout.buffer
sys.stdout = sys.stderr          # печать библиотек уходит в лог, а не в канал с ответами

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from core import asr_fix

# Названия моделей для каждого движка: на видеокарте берём крупную, на процессоре - компактную.
FASTER_CUDA, FASTER_CPU = "large-v3-turbo", "small"
HF_CUDA, HF_CPU = "openai/whisper-large-v3-turbo", "openai/whisper-small"


def log(msg):
    print("[whisper] " + msg, file=sys.stderr, flush=True)


def send(obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    REAL_OUT.write(struct.pack("<I", len(data)) + data)
    REAL_OUT.flush()


def read_exact(stream, n):
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def app_config():
    try:
        with open(os.path.join(BASE, "config.json"), encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


_CUDA = None


def _has_cuda():
    """Есть ли видеокарта NVIDIA. Проверяем через ctranslate2, torch для этого не нужен."""
    global _CUDA
    if _CUDA is None:
        try:
            import ctranslate2
            _CUDA = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            _CUDA = False
    return _CUDA


# ------------------------------------------------------------------ движки
class FasterEngine:
    """faster-whisper: CTranslate2, быстрый старт и быстрый счёт. Основной путь."""

    name = "faster-whisper"

    def __init__(self, model_id, cfg):
        from faster_whisper import WhisperModel
        cuda = _has_cuda()
        if not model_id or model_id == "auto":
            model_id = FASTER_CUDA if cuda else FASTER_CPU
        device, compute = ("cuda", "float16") if cuda else ("cpu", "int8")
        threads = max(2, min(8, (os.cpu_count() or 4)))
        log(f"загружаю faster-whisper {model_id} на {device} ({compute}, потоков {threads})")
        t0 = time.time()
        self.model = WhisperModel(model_id, device=device, compute_type=compute, cpu_threads=threads)
        log(f"загружено за {time.time() - t0:.1f} с")
        self.device, self.model_id = device, model_id

    def once(self, audio, language, prompt):
        segs, info = self.model.transcribe(
            audio, language=language, beam_size=1, temperature=0.0,
            initial_prompt=prompt or None, vad_filter=False,
            condition_on_previous_text=False)
        parts, logs = [], []
        for s in segs:
            parts.append(s.text)
            if s.avg_logprob is not None:
                logs.append(s.avg_logprob)
        text = "".join(parts).strip()
        score = (sum(logs) / len(logs)) if logs else -1.0
        return {"text": text, "lang": info.language or "",
                "lang_prob": float(info.language_probability or 0.0), "score": score}


class TransformersEngine:
    """Запасной путь: transformers + torch. Медленнее, но работает, если faster-whisper не поставлен."""

    name = "transformers"

    def __init__(self, model_id, cfg):
        import torch
        from transformers import pipeline
        cuda = torch.cuda.is_available()
        if not model_id or model_id == "auto":
            model_id = HF_CUDA if cuda else HF_CPU
        log(f"загружаю transformers {model_id} на {'cuda' if cuda else 'cpu'}")
        t0 = time.time()
        self.asr = pipeline("automatic-speech-recognition", model=model_id,
                            torch_dtype=torch.float16 if cuda else torch.float32,
                            device="cuda:0" if cuda else "cpu")
        log(f"загружено за {time.time() - t0:.1f} с")
        self.device, self.model_id = ("cuda" if cuda else "cpu"), model_id
        self._prompt = {"ids": None, "text": "", "done": False}

    def _prompt_ids(self, prompt):
        if not self._prompt["done"]:
            self._prompt["done"] = True
            try:
                self._prompt["text"] = prompt or ""
                if prompt:
                    ids = self.asr.tokenizer.get_prompt_ids(prompt, return_tensors="pt")
                    if hasattr(ids, "to"):
                        ids = ids.to(self.asr.device)
                    self._prompt["ids"] = ids
            except Exception as e:
                log(f"подсказка не включена: {e}")
                self._prompt["ids"] = None
        return self._prompt["ids"]

    def once(self, audio, language, prompt):
        gk = {"task": "transcribe", "num_beams": 1}
        if language:
            gk["language"] = asr_fix.LANG_NAMES.get(language, "russian")
        # language не задан -> автоопределение (ключ не передаём: часть версий падает на language=None)
        pid = self._prompt_ids(prompt)
        if pid is not None:
            gk["prompt_ids"] = pid
        out = self.asr({"raw": audio, "sampling_rate": 16000}, generate_kwargs=gk, return_timestamps=False)
        text = (out.get("text") or "").strip()
        if prompt and text.startswith(prompt[:25]):          # эхо подсказки на тишине
            text = text[len(prompt):].strip() if len(text) > len(prompt) + 3 else ""
        return {"text": text, "lang": language or "", "lang_prob": 0.0, "score": 0.0}


def load_engine(model_id, cfg):
    """faster-whisper предпочтителен: быстрый старт. Если его нет - откат на transformers."""
    prefer = str(cfg.get("whisper_engine", "auto")).lower()
    order = ([FasterEngine, TransformersEngine] if prefer != "transformers"
             else [TransformersEngine, FasterEngine])
    last = None
    for cls in order:
        try:
            return cls(model_id, cfg)
        except ImportError as e:
            last = e
            log(f"{cls.name} недоступен ({e}), пробую другой движок")
        except Exception as e:
            last = e
            log(f"{cls.name} не запустился: {e}")
    raise RuntimeError("Не удалось запустить распознавание речи: " + str(last))


# ------------------------------------------------------------------ распознавание
def transcribe(engine, pcm, plan, hotwords, prompt, fixes):
    import numpy as np
    a = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    if a.size < 1600:
        return {"text": "", "lang": "", "lang_prob": 0.0}
    peak = float(np.max(np.abs(a)))
    if peak < 1e-3:
        return {"text": "", "lang": "", "lang_prob": 0.0}
    a = a * min(40.0, 0.9 / peak)             # тихую речь и шёпот поднимаем до нормальной громкости

    first = engine.once(a, plan["primary"], prompt)
    cands = [first]
    if plan["extra"] and asr_fix.is_unsure(first.get("lang"), first.get("lang_prob")):
        for lang in plan["extra"]:
            if lang == first.get("lang"):
                continue
            try:
                cands.append(engine.once(a, lang, prompt))
            except Exception:
                log(f"дополнительный проход ({lang}) не удался")
    best = asr_fix.pick_best(cands, hotwords)
    text = asr_fix.apply_fixes(best.get("text", ""), fixes)
    return {"text": text, "lang": best.get("lang", ""), "lang_prob": best.get("lang_prob", 0.0)}


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    cfg = app_config()
    engine = load_engine(args[0] if args else "auto", cfg)
    if "--download" in sys.argv:
        log("модель скачана и загружается нормально")
        return

    import numpy as np
    hotwords = asr_fix.load_hotwords(BASE)
    fixes = asr_fix.load_fixes(BASE)
    plan = asr_fix.language_plan(cfg.get("asr_language", "auto"),
                                 bool(cfg.get("asr_second_pass", True)))
    prompt = asr_fix.prompt_text(hotwords)
    log(f"язык: {cfg.get('asr_language', 'auto')}, проходы: {plan}, подсказка из {len(hotwords)} названий")

    transcribe(engine, (np.random.randn(16000) * 800).astype("<i2").tobytes(),
               plan, hotwords, prompt, fixes)      # прогрев: первая фраза не тормозит
    send({"ready": True, "device": engine.device, "model": engine.model_id, "engine": engine.name})

    stdin = sys.stdin.buffer
    while True:
        head = read_exact(stdin, 4)
        if head is None:
            break
        n = struct.unpack("<I", head)[0]
        if n == 0:
            break
        pcm = read_exact(stdin, n)
        if pcm is None:
            break
        try:
            send(transcribe(engine, pcm, plan, hotwords, prompt, fixes))
        except Exception:
            traceback.print_exc()
            send({"text": "", "error": True})


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
