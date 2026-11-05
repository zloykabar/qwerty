# -*- coding: utf-8 -*-
"""
Озвучка для Джарвиса.
  * PiperSpeaker: живой нейросетевой голос (Piper), работает офлайн, говорит по предложениям
    без пауз: пока звучит одно предложение, синтезируется следующее.
  * SapiSpeaker: запасной вариант на встроенных голосах Windows.
Оба класса имеют одинаковые методы: speak(text, wait), wait_idle(), stop(), apply().
"""
import array
import json
import math
import os
import queue
import re
import struct
import subprocess
import sys
import threading
import time
import traceback

from core.lazy import Lazy
sd = Lazy("sounddevice")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PIPER_DIR = os.path.join(BASE_DIR, "piper")
VOICES_DIR = os.path.join(BASE_DIR, "voices")
XTTS_DIR = os.path.join(BASE_DIR, "voices_xtts")          # сюда кладут образцы голоса (.wav)
XTTS_PY = os.path.join(BASE_DIR, "xtts_env", "Scripts", "python.exe")
XTTS_WORKER = os.path.join(BASE_DIR, "xtts_worker.py")
XTTS_LOG = os.path.join(BASE_DIR, "xtts.log")
XTTS_OK = os.path.join(BASE_DIR, "xtts_env", "installed.ok")   # создаёт Install_XTTS.bat после успешной установки


def xtts_log_tail(n=12):
    try:
        with open(XTTS_LOG, encoding="utf-8", errors="replace") as f:
            return "\n".join(f.read().strip().splitlines()[-n:])
    except Exception:
        return ""
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
RU_KEYS = ("ru", "russian", "irina", "pavel")


def log(msg):
    print("[голос] " + msg, file=sys.stderr)


def split_sentences(text, maxlen=220):
    """Режем текст на предложения не длиннее maxlen символов."""
    parts = re.split(r"(?<=[.!?…])\s+|\n+", text.strip())
    out = []
    for p in parts:
        p = p.strip()
        while len(p) > maxlen:
            cut = max(p.rfind(", ", 0, maxlen), p.rfind(" ", 0, maxlen))
            if cut < 40:
                cut = maxlen
            out.append(p[:cut].strip())
            p = p[cut:].lstrip(", ")
        if p:
            out.append(p)
    return out


def speech_clean(text):
    """Убираем всё, что нельзя произнести: markdown, ссылки, эмодзи."""
    t = re.sub(r"[*_`#>\[\]{}|~^]", "", text)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", t)
    return re.sub(r"\s+", " ", t).strip()


# ======================================================================
class PiperSpeaker:
    engine = "piper"
    maxlen = 220

    def __init__(self, core):
        self.core = core
        self.exe = os.path.join(PIPER_DIR, "piper.exe")
        self.ready = threading.Event()
        self.ok = False
        self.voices = []
        self.cond = threading.Condition()
        self.pending = 0
        self.gen = 0
        self.synth_q = queue.Queue()
        self.play_q = queue.Queue()
        self.raw_flag = "--output-raw"
        self.out = None
        self.out_sr = None
        threading.Thread(target=self._init, daemon=True).start()

    # ----- запуск -----
    def _init(self):
        try:
            names = {"ruslan": "Руслан", "denis": "Денис", "dmitri": "Дмитрий", "irina": "Ирина"}
            for f in sorted(os.listdir(VOICES_DIR)):
                if f.endswith(".onnx") and os.path.exists(os.path.join(VOICES_DIR, f + ".json")):
                    parts = f[:-5].split("-")
                    nice = names.get(parts[1], parts[1]) if len(parts) > 1 else f[:-5]
                    quality = parts[-1] if len(parts) > 2 else ""
                    self.voices.append({"id": f, "name": f"{nice} (нейросеть, {quality})".replace(", )", ")"),
                                        "ru": True})
            if self.voices:
                t0 = time.time()
                pcm, _ = self._synthesize("Проверка связи.")
                if pcm:
                    self.ok = True
                    threading.Thread(target=self._synth_loop, daemon=True).start()
                    threading.Thread(target=self._play_loop, daemon=True).start()
                    log(f"Piper готов за {time.time() - t0:.1f} с, голосов: {len(self.voices)}")
                else:
                    log("Piper не вернул звук при проверке")
        except Exception:
            traceback.print_exc()
        finally:
            self.ready.set()
            self.core.emit("voices")

    def _current(self):
        vid = self.core.settings.get("voice_id") or ""
        ids = [v["id"] for v in self.voices]
        if vid not in ids:
            pref = (self.core.cfg.get("piper_voice") or "") + ".onnx"
            vid = pref if pref in ids else ids[0]
        model = os.path.join(VOICES_DIR, vid)
        sr = 22050
        try:
            with open(model + ".json", encoding="utf-8") as f:
                sr = int(json.load(f)["audio"]["sample_rate"])
        except Exception:
            pass
        return model, model + ".json", sr

    @staticmethod
    def _scale(pcm, vol):
        a = array.array("h")
        a.frombytes(pcm[: len(pcm) // 2 * 2])
        k = vol / 100.0
        return array.array("h", (int(x * k) for x in a)).tobytes()

    def _synthesize(self, text):
        """Возвращает (сырое аудио 16 бит, частота). Пустые байты = ошибка."""
        model, cfgp, sr = self._current()
        s = self.core.settings
        length = max(0.6, min(1.8, 180.0 / max(80, int(s["rate"]))))
        flags = [self.raw_flag, "--output_raw" if self.raw_flag == "--output-raw" else "--output-raw"]
        out = b""
        for flag in flags:
            cmd = [self.exe, "--model", model, "--config", cfgp, flag,
                   "--length_scale", f"{length:.2f}",
                   "--noise_scale", str(self.core.cfg.get("piper_noise_scale", 0.75)),
                   "--noise_w", str(self.core.cfg.get("piper_noise_w", 0.9))]
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, cwd=PIPER_DIR, creationflags=NO_WINDOW)
            out, err = p.communicate((text + "\n").encode("utf-8"), timeout=90)
            if out:
                self.raw_flag = flag
                break
            log("piper молчит: " + err.decode("utf-8", "replace")[-300:])
        if not out:
            return b"", sr
        vol = max(0, min(100, int(s["volume"])))
        if vol < 100:
            out = self._scale(out, vol)
        return out + b"\x00\x00" * int(sr * 0.10), sr

    # ----- конвейер: синтез -> воспроизведение -----
    def _done(self):
        with self.cond:
            self.pending = max(0, self.pending - 1)
            self.cond.notify_all()

    def _synth_loop(self):
        while True:
            gen, text = self.synth_q.get()
            if gen != self.gen:
                self._done()
                continue
            try:
                pcm, sr = self._synthesize(text)
            except Exception:
                traceback.print_exc()
                pcm, sr = b"", 22050
            self.play_q.put((gen, pcm, sr))

    def _close_out(self, abort=False):
        """Закрывать аудиопоток можно только из потока воспроизведения: закрытие из другого потока
        во время записи звука роняет всю программу (ошибка PortAudio без сообщения)."""
        o, self.out = self.out, None
        if o is not None:
            try:
                if abort:
                    o.abort()
                o.close()
            except Exception:
                pass

    def _play_loop(self):
        while True:
            gen, pcm, sr = self.play_q.get()
            try:
                if gen == self.gen and pcm:
                    if self.out is None or self.out_sr != sr:
                        self._close_out()
                        self.out = sd.RawOutputStream(samplerate=sr, channels=1, dtype="int16")
                        self.out.start()
                        self.out_sr = sr
                    step = max(2, int(sr * 0.06) * 2)          # кусочки по 60 мс: между ними проверяем «стоп»
                    interrupted = False
                    for i in range(0, len(pcm), step):
                        if gen != self.gen:
                            interrupted = True
                            break
                        self.out.write(pcm[i:i + step])
                    if interrupted:
                        self._close_out(abort=True)             # сбросит недопроигранный звук
            except Exception:
                traceback.print_exc()
                self._close_out()                                # поток сломался: откроем заново
            finally:
                self._done()

    # ----- общий интерфейс -----
    def speak(self, text, wait=True):
        if not self.ok:
            return
        chunks = split_sentences(text, self.maxlen)
        if not chunks:
            return
        with self.cond:
            self.pending += len(chunks)
        for c in chunks:
            self.synth_q.put((self.gen, c))
        if wait:
            self.wait_idle(timeout=30 + len(text) * 0.5)

    def wait_idle(self, timeout=None):
        with self.cond:
            self.cond.wait_for(lambda: self.pending <= 0, timeout)

    def stop(self):
        """Только поднимает «флаг» и чистит очереди. Звук прервёт сам поток воспроизведения."""
        self.gen += 1
        for q in (self.synth_q, self.play_q):
            try:
                while True:
                    q.get_nowait()
                    self._done()
            except queue.Empty:
                pass

    def apply(self):
        pass        # настройки читаются при каждом синтезе


# ======================================================================
class SapiSpeaker:
    """Голоса Windows (SAPI). Живёт в своём потоке, чтобы зависший голос не вешал Джарвиса."""
    engine = "sapi"

    def __init__(self, core):
        self.core = core
        self.q = queue.Queue()
        self.ready = threading.Event()
        self.ok = False
        self.voices = []
        self.v = None
        self.toks = []
        self._stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        try:
            import comtypes
            import comtypes.client
            comtypes.CoInitialize()
            t0 = time.time()
            self.v = comtypes.client.CreateObject("SAPI.SpVoice")
            self.toks = list(self.v.GetVoices())
            for t in self.toks:
                name = t.GetDescription()
                self.voices.append({"id": t.Id, "name": name,
                                    "ru": any(k in (t.Id + name).lower() for k in RU_KEYS)})
            self._apply()
            self.ok = True
            log(f"SAPI готов за {time.time() - t0:.1f} с, голосов: {len(self.voices)}")
        except Exception:
            traceback.print_exc()
        finally:
            self.ready.set()
            self.core.emit("voices")
        while True:
            kind, text, done = self.q.get()
            try:
                if self.ok:
                    if kind == "apply":
                        self._apply()
                    elif kind == "speak":
                        self._speak(text)
            except Exception:
                traceback.print_exc()
            finally:
                done.set()

    def _apply(self):
        s = self.core.settings
        vid = s.get("voice_id")
        chosen = next((t for t in self.toks if t.Id == vid), None) if vid else None
        if chosen is None:
            for t, info in zip(self.toks, self.voices):
                if info["ru"]:
                    chosen = t
                    break
        if chosen is not None:
            self.v.Voice = chosen
        self.v.Rate = max(-10, min(10, int(round(math.log(max(80, int(s["rate"])) / 200.0, 1.1)))))
        self.v.Volume = max(0, min(100, int(s["volume"])))

    def _speak(self, text):
        self._stop = False
        self.v.Speak(text, 1)                      # 1 = асинхронно, ждём с ограничением по времени
        limit = 10 + len(text) * 0.3
        t0 = time.time()
        while True:
            try:
                if self.v.WaitUntilDone(200):
                    break
            except Exception:
                break
            if self._stop or time.time() - t0 > limit:
                self.v.Speak("", 3)                # 3 = сбросить очередь
                break

    def _job(self, kind, text="", wait=True, timeout=None):
        done = threading.Event()
        self.q.put((kind, text, done))
        if wait:
            done.wait(timeout=timeout or (20 + len(text) * 0.3))

    def speak(self, text, wait=True):
        for c in split_sentences(text, 400):
            self._job("speak", c, wait=False)
        if wait:
            self.wait_idle(timeout=30 + len(text) * 0.5)

    def wait_idle(self, timeout=None):
        self._job("noop", wait=True, timeout=timeout or 120)

    def stop(self):
        self._stop = True
        try:
            while True:
                _, _, done = self.q.get_nowait()
                done.set()
        except queue.Empty:
            pass

    def apply(self):
        self._job("apply", wait=False)


# ======================================================================
class XttsSpeaker(PiperSpeaker):
    """Клонирование голоса (XTTS v2). Модель живёт в отдельной программе xtts_worker.py
    внутри xtts_env (Python 3.11), потому что на Python 3.14 XTTS не запускается.
    Конвейер (по предложениям, без пауз) берём у PiperSpeaker."""
    engine = "xtts"
    maxlen = 170            # XTTS плохо читает очень длинные предложения

    def __init__(self, core):
        self.proc = None
        self.proc_lock = threading.Lock()
        self.out_sr_default = 24000
        super().__init__(core)

    # ----- фоновая программа -----
    def _kill(self):
        p, self.proc = self.proc, None
        if p is not None:
            try:
                p.stdin.close()
            except Exception:
                pass
            try:
                p.kill()
            except Exception:
                pass

    def close(self):
        self._kill()

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                raise RuntimeError("XTTS закрылся. Последние строки xtts.log:\n" + xtts_log_tail()
                                   + "\nЕсли там ModuleNotFoundError, запустите Install_XTTS.bat ещё раз.")
            buf += chunk
        return buf

    def _read_frame(self):
        n = struct.unpack("<I", self._read_exact(4))[0]
        return self._read_exact(n) if n else b""

    def _start_worker(self):
        self._kill()
        env = dict(os.environ, COQUI_TOS_AGREED="1", PYTHONIOENCODING="utf-8")
        errf = open(XTTS_LOG, "ab")
        self.proc = subprocess.Popen([XTTS_PY, XTTS_WORKER], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=errf, cwd=BASE_DIR,
                                     env=env, creationflags=NO_WINDOW)
        hello = json.loads(self._read_frame().decode("utf-8"))      # ждём загрузку модели
        self.out_sr_default = int(hello.get("sr", 24000))
        log(f"XTTS загружен, устройство: {hello.get('device')}")

    # ----- запуск -----
    def _init(self):
        try:
            for f in sorted(os.listdir(XTTS_DIR)):
                if f.lower().endswith(".wav"):
                    self.voices.append({"id": f, "name": f"{f[:-4]} (клон голоса, XTTS)", "ru": True})
            if not self.voices:
                log("в папке voices_xtts нет .wav образца")
            else:
                t0 = time.time()
                with self.proc_lock:
                    self._start_worker()
                pcm, _ = self._synthesize("Проверка связи.")
                if pcm:
                    self.ok = True
                    threading.Thread(target=self._synth_loop, daemon=True).start()
                    threading.Thread(target=self._play_loop, daemon=True).start()
                    log(f"XTTS готов за {time.time() - t0:.1f} с, образцов голоса: {len(self.voices)}")
                else:
                    log("XTTS не вернул звук при проверке")
        except Exception:
            traceback.print_exc()
        finally:
            self.ready.set()
            self.core.emit("voices")

    def _synthesize(self, text):
        sr = self.out_sr_default
        s = self.core.settings
        ids = [v["id"] for v in self.voices]
        if not ids:
            return b"", sr
        vid = s.get("voice_id") or ""
        if vid not in ids:
            pref = (self.core.cfg.get("xtts_voice") or "")
            vid = pref if pref in ids else ids[0]
        speed = max(0.7, min(1.4, int(s["rate"]) / 180.0))
        req = json.dumps({"text": text, "wav": os.path.join(XTTS_DIR, vid), "speed": speed},
                         ensure_ascii=False) + "\n"
        with self.proc_lock:
            try:
                if self.proc is None or self.proc.poll() is not None:
                    self._start_worker()
                self.proc.stdin.write(req.encode("utf-8"))
                self.proc.stdin.flush()
                out = self._read_frame()
            except Exception:
                traceback.print_exc()
                self._kill()
                return b"", sr
        if not out:
            return b"", sr
        vol = max(0, min(100, int(s["volume"])))
        if vol < 100:
            out = self._scale(out, vol)
        return out + b"\x00\x00" * int(sr * 0.10), sr


# ======================================================================
def xtts_installed():
    has_wav = os.path.isdir(XTTS_DIR) and any(f.lower().endswith(".wav") for f in os.listdir(XTTS_DIR))
    if os.path.exists(XTTS_PY) and has_wav and not os.path.exists(XTTS_OK):
        log("XTTS установлен не до конца: запустите Install_XTTS.bat и дождитесь слова «Готово»")
        return False
    return os.path.exists(XTTS_PY) and has_wav


def piper_installed():
    has_voice = os.path.isdir(VOICES_DIR) and any(f.endswith(".onnx") for f in os.listdir(VOICES_DIR))
    return os.path.exists(os.path.join(PIPER_DIR, "piper.exe")) and has_voice


def use_xtts(cfg):
    return str(cfg.get("tts_engine", "auto")).lower() in ("auto", "xtts") and xtts_installed()


def make_speaker(core, fast=False):
    """XTTS (клон голоса), если установлен (Install_XTTS.bat + .wav в voices_xtts);
    иначе Piper (INSTALL_JARVIS.bat); иначе голоса Windows.
    fast=True: клон голоса пропускаем, берём Piper/Windows, они готовы за секунду.
    Клон голоса Джарвис загружает потом в фоне и подключает сам.
    В config.json: "tts_engine": "auto" | "xtts" | "piper" | "sapi"."""
    mode = str(core.cfg.get("tts_engine", "auto")).lower()
    if not fast and use_xtts(core.cfg):
        return XttsSpeaker(core)
    if mode in ("auto", "xtts", "piper") and piper_installed():
        return PiperSpeaker(core)
    return SapiSpeaker(core)


def make_fallback(core, failed):
    """Что включить, если выбранный движок не запустился."""
    if failed == "xtts" and piper_installed():
        return PiperSpeaker(core)
    return SapiSpeaker(core)
