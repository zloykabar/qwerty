# -*- coding: utf-8 -*-
"""
Распознавание речи Джарвиса.
 - VoiceGate: адаптивный детектор речи (подстраивается под шум комнаты, ловит тихую речь и шёпот)
 - WhisperRecognizer: режет звук на фразы и отправляет их в Whisper (отдельная программа whisper_worker.py)
 - VoskRecognizer: запасной вариант, с автоусилением тихого голоса
У обоих одинаковый интерфейс: accept(блок звука) -> текст фразы или None, reset().
"""
import array
import collections
import json
import os
import re
import struct
import subprocess
import threading
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
XTTS_PY = os.path.join(BASE_DIR, "xtts_env", "Scripts", "python.exe")
WHISPER_WORKER = os.path.join(BASE_DIR, "whisper_worker.py")
WHISPER_LOG = os.path.join(BASE_DIR, "whisper.log")
WHISPER_OK = os.path.join(BASE_DIR, "xtts_env", "whisper.ok")      # создаёт Install_Whisper.bat
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def whisper_installed():
    return os.path.exists(XTTS_PY) and os.path.exists(WHISPER_OK)


def _rms(samples):
    sub = samples[::2]
    if not len(sub):
        return 0.0
    return (sum(s * s for s in sub) / len(sub)) ** 0.5


def _log_tail(n=10):
    try:
        with open(WHISPER_LOG, encoding="utf-8", errors="replace") as f:
            return "\n".join(f.read().strip().splitlines()[-n:])
    except Exception:
        return ""


# ---------------------------------------------------------------- Whisper
class WhisperClient:
    def __init__(self, model="auto"):
        self.model = model or "auto"
        self.proc = None
        self.lock = threading.Lock()
        self.ok = False
        self.device = ""
        self.fails = 0

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                raise RuntimeError("Whisper закрылся. Последние строки whisper.log:\n" + _log_tail())
            buf += chunk
        return buf

    def _read_json(self):
        n = struct.unpack("<I", self._read_exact(4))[0]
        return json.loads(self._read_exact(n).decode("utf-8")) if n else {}

    def start(self):
        """Запускает Whisper и ждёт, пока модель загрузится."""
        env = dict(os.environ, PYTHONIOENCODING="utf-8", HF_HUB_DISABLE_SYMLINKS_WARNING="1")
        errf = open(WHISPER_LOG, "ab")
        self.proc = subprocess.Popen([XTTS_PY, WHISPER_WORKER, self.model], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=errf, cwd=BASE_DIR, env=env,
                                     creationflags=NO_WINDOW)
        hello = self._read_json()
        self.device = hello.get("device", "")
        self.ok = True

    def transcribe(self, pcm):
        with self.lock:
            try:
                self.proc.stdin.write(struct.pack("<I", len(pcm)) + pcm)
                self.proc.stdin.flush()
                res = self._read_json()
                self.fails = 0
                return res.get("text", "")
            except Exception:
                self.fails += 1
                if self.fails >= 2:
                    self.ok = False
                raise

    def close(self):
        p, self.proc, self.ok = self.proc, None, False
        if p is not None:
            try:
                p.stdin.write(struct.pack("<I", 0))
                p.stdin.close()
            except Exception:
                pass
            try:
                p.kill()
            except Exception:
                pass


JUNK = re.compile(r"(?i)(субтитр|продолжение следует|спасибо за просмотр|подписывайтесь|редактор|корректор|"
                  r"dimatorzok|amara\.org|благодарю за внимание|до новых встреч)")


def clean_text(t):
    """Whisper на шуме иногда «слышит» фразы из субтитров или зацикливается на одном слове."""
    t = (t or "").strip()
    if not t or not re.search(r"\w", t) or JUNK.search(t):
        return ""
    words = re.findall(r"\w+", t.lower())
    if len(words) >= 6:
        uniq = len(set(words))
        if uniq <= 2 or uniq / len(words) < 0.2:       # «да да да да», «Windows, Windows, Windows…»
            return ""
    return t


# ---------------------------------------------------------------- детектор речи
class VoiceGate:
    """Режет поток звука на фразы. Порог речи подстраивается под шум комнаты,
    поэтому работает и для обычного голоса, и для тихого, и для шёпота."""
    MULT = {1: 3.2, 2: 2.6, 3: 2.1, 4: 1.7, 5: 1.4}      # чем больше чувствительность, тем ниже порог

    def __init__(self, sensitivity=3, block_sec=0.1, max_sec=14.0, end_silence=0.7):
        self.mult = self.MULT.get(int(sensitivity), 2.1)
        self.silence_blocks = max(2, int(end_silence / block_sec))
        self.max_blocks = int(max_sec / block_sec)
        self.preroll = collections.deque(maxlen=4)       # 0,4 с до начала речи, чтобы не терять первое слово
        self.floor = None
        self.reset()

    def reset(self):
        self.active = False
        self.buf = []
        self.hot = 0
        self.silence = 0
        self.voiced = 0
        self.end_thr = 0.0
        self.preroll.clear()

    def level(self):
        return self.floor or 0.0

    def feed(self, block):
        s = array.array("h")
        s.frombytes(block[: len(block) // 2 * 2])
        r = _rms(s)
        self.preroll.append(block)
        if not self.active:
            if self.floor is None:
                self.floor = max(20.0, r)
            elif r < self.floor:
                self.floor = 0.8 * self.floor + 0.2 * r              # шум стал тише: быстро подстраиваемся
            else:
                self.floor = min(1500.0, self.floor * 0.995 + r * 0.005)   # громче: медленно, чтобы речь не стала «шумом»
            start_thr = self.floor * self.mult + 30
            if r > start_thr:
                self.hot += 1
                if self.hot >= 2:
                    self.active = True
                    self.buf = list(self.preroll)
                    self.silence = 0
                    self.voiced = self.hot
                    self.end_thr = start_thr * 0.6
            else:
                self.hot = 0
            return None
        self.buf.append(block)
        if r > self.end_thr:
            self.silence = 0
            self.voiced += 1
        else:
            self.silence += 1
        if self.silence >= self.silence_blocks or len(self.buf) >= self.max_blocks:
            return self._finish()
        return None

    def _finish(self):
        buf, silence, voiced = self.buf, self.silence, self.voiced
        self.active, self.buf, self.hot, self.silence, self.voiced = False, [], 0, 0, 0
        self.preroll.clear()
        if voiced < 3:                                  # щелчок или кашель, а не речь
            return None
        if silence > 3:                                 # хвост тишины лишний, оставляем 0,3 с
            buf = buf[: len(buf) - (silence - 3)]
        return b"".join(buf)


class WhisperRecognizer:
    def __init__(self, client, sensitivity=3):
        self.client = client
        self.gate = VoiceGate(sensitivity)

    def accept(self, block):
        seg = self.gate.feed(block)
        if seg is None:
            return None
        try:
            return clean_text(self.client.transcribe(seg))
        except Exception:
            traceback.print_exc()
            return ""

    def reset(self):
        self.gate.reset()


# ---------------------------------------------------------------- Vosk с автоусилением
class AutoGain:
    """Подтягивает тихий голос до нормальной громкости. Усиление запоминается между фразами
    и не раздувается на тишине, поэтому шум не усиливается сильнее, чем речь."""
    def __init__(self, target=2500.0, max_gain=18.0):
        self.target, self.max_gain = target, max_gain
        self.floor = None
        self.speech = 1000.0
        self.gain = 2.0

    def process(self, block):
        s = array.array("h")
        s.frombytes(block[: len(block) // 2 * 2])
        r = _rms(s)
        if self.floor is None:
            self.floor = max(20.0, r)
        elif r < self.floor:
            self.floor = 0.8 * self.floor + 0.2 * r
        else:
            self.floor = min(1500.0, self.floor * 0.995 + r * 0.005)
        raw_speech = r > self.floor * 1.8 + 25
        if raw_speech:
            self.speech = 0.9 * self.speech + 0.1 * r
        want = min(self.max_gain, max(1.0, self.target / max(self.speech, 1.0)))
        self.gain += (want - self.gain) * 0.2
        g = self.gain
        if g < 1.05:
            return block
        return array.array("h", (32767 if v * g > 32767 else -32768 if v * g < -32768 else int(v * g)
                                 for v in s)).tobytes()


class VoskRecognizer:
    def __init__(self, kaldi_rec, boost=True):
        self.rec = kaldi_rec
        self.agc = AutoGain() if boost else None

    def accept(self, block):
        if self.agc is not None:
            block = self.agc.process(block)
        if self.rec.AcceptWaveform(block):
            return json.loads(self.rec.Result()).get("text", "")
        return None

    def reset(self):
        self.rec.Reset()
