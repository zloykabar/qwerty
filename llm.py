# -*- coding: utf-8 -*-
"""Работа с Ollama: автозапуск, скачивание модели, обычные и потоковые ответы."""
import json
import os
import shutil
import subprocess
import time

from core.lazy import Lazy
requests = Lazy("requests")

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Ollama:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base = cfg["ollama_url"].split("/api/")[0].rstrip("/")
        self.proc = None            # процесс, который запустили мы (его же и остановим)

    # ----- состояние -----
    def up(self):
        try:
            return requests.get(self.base + "/api/tags", timeout=2).status_code == 200
        except Exception:
            return False

    @staticmethod
    def find_exe():
        p = shutil.which("ollama")
        if p:
            return p
        for env in ("LOCALAPPDATA", "ProgramFiles"):
            root = os.environ.get(env, "")
            for sub in (("Programs", "Ollama", "ollama.exe"), ("Ollama", "ollama.exe")):
                c = os.path.join(root, *sub)
                if os.path.exists(c):
                    return c
        return None

    def have(self, model):
        tags = requests.get(self.base + "/api/tags", timeout=5).json().get("models", [])
        names = {m.get("name", "") for m in tags} | {m.get("model", "") for m in tags}
        return (model if ":" in model else model + ":latest") in names

    def pull(self, model, emit):
        emit("sys", f"Скачиваю модель {model}. Это делается один раз, может занять время.")
        last = -10
        with requests.post(self.base + "/api/pull", json={"model": model, "name": model, "stream": True},
                           stream=True, timeout=(10, None)) as r:
            r.raise_for_status()
            for line in r.iter_lines(chunk_size=64):
                if not line:
                    continue
                d = json.loads(line)
                if d.get("error"):
                    raise RuntimeError(d["error"])
                if d.get("total"):
                    pct = int(d.get("completed", 0) * 100 / d["total"])
                    if pct >= last + 10:
                        last = pct
                        emit("sys", f"Модель {model}: {pct}%")

    def warm(self, model):
        """Загружаем модель в память заранее, чтобы первый ответ не ждал."""
        try:
            requests.post(self.base + "/api/generate", json={"model": model, "keep_alive": "30m"}, timeout=300)
        except Exception:
            pass

    def ensure(self, emit, models):
        """Запускает Ollama (если нужно) и скачивает модели. Возвращает текст ошибки или None."""
        if not self.up():
            if not self.cfg.get("auto_start_ollama", True):
                return "Ollama не запущена. Запустите её или включите автозапуск в config.json."
            exe = self.find_exe()
            if not exe:
                emit("err", "Ollama не найдена. Установите её с ollama.com/download и перезапустите Джарвиса.")
                return "Не нашёл Ollama. Установите её с официального сайта."
            emit("sys", "Запускаю Ollama…")
            self.proc = subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            for _ in range(80):
                if self.up() or self.proc.poll() is not None:
                    break
                time.sleep(0.5)
            if not self.up():
                emit("err", "Ollama не запустилась за 40 секунд. Попробуйте открыть её вручную из меню Пуск.")
                return "Ollama не запустилась."
            emit("sys", "Ollama запущена.")
        for m in models:
            if not self.have(m):
                if not self.cfg.get("auto_pull_model", True):
                    return f"Модель {m} не скачана. Выполните в командной строке: ollama pull {m}"
                self.pull(m, emit)
        self.warm(models[0])
        emit("sys", "Нейросеть готова.")
        return None

    def stop(self):
        """Останавливаем Ollama, только если запускали её сами."""
        if self.proc is not None and self.cfg.get("stop_ollama_on_exit", True) and self.proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=NO_WINDOW)

    # ----- ответы -----
    def _payload(self, messages, model, temperature, num_predict, num_ctx, stream):
        opts = {"temperature": temperature, "num_ctx": num_ctx}
        if num_predict:
            opts["num_predict"] = int(num_predict)
        return {"model": model or self.cfg["ollama_model"], "messages": messages,
                "stream": stream, "keep_alive": "30m", "options": opts}

    def chat(self, messages, model=None, temperature=0.8, num_predict=None, num_ctx=4096, timeout=180):
        r = requests.post(self.base + "/api/chat",
                          json=self._payload(messages, model, temperature, num_predict, num_ctx, False),
                          timeout=timeout)
        r.raise_for_status()
        return r.json()["message"]["content"].strip()

    def stream(self, messages, model=None, temperature=0.8, num_predict=None, num_ctx=4096):
        with requests.post(self.base + "/api/chat",
                           json=self._payload(messages, model, temperature, num_predict, num_ctx, True),
                           stream=True, timeout=(10, 300)) as r:
            r.raise_for_status()
            for line in r.iter_lines(chunk_size=64):
                if not line:
                    continue
                d = json.loads(line)
                if d.get("error"):
                    raise RuntimeError(d["error"])
                piece = d.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if d.get("done"):
                    break
