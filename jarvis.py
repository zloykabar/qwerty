# -*- coding: utf-8 -*-
"""
Джарвис 2.0: голосовой помощник для Windows.
Интерфейс: pywebview (HTML) | Слух: Vosk | Голос: Windows SAPI | Мозг: Ollama | Расширения: папка plugins
"""
import array
import ctypes
import datetime
import difflib
import importlib.util
import json
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "jarvis.log")

# pythonw не имеет консоли: пишем ошибки в logs/jarvis.log, чтобы их можно было прочитать
if sys.stdout is None or sys.stderr is None:
    os.makedirs(LOG_DIR, exist_ok=True)
    _log = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def _show_fatal(title, text):
    """Под pythonw консоли нет: без этого ошибка запуска была бы невидимой. Показываем окно Windows и пишем в logs\\crash.log."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "crash.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + text + "\n" + "-" * 60 + "\n")
    except Exception:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(0, text[-1500:], title, 0x10)
    except Exception:
        pass


def _excepthook(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    hint = ""
    if issubclass(exc_type, ImportError):
        hint = ("Не хватает библиотеки Python. Запустите INSTALL_JARVIS.bat ещё раз: он докачает недостающее.\n\n")
    _show_fatal("Джарвис: ошибка запуска",
                hint + "Подробности записаны в файл logs\\crash.log рядом с программой.\n\n" + text)


sys.excepthook = _excepthook


def _single_instance():
    """True, если это первый запуск. Второй экземпляр (например, автозагрузка + ярлык) сразу выходит."""
    try:
        # use_last_error=True обязателен: иначе любой промежуточный вызов ctypes может перезаписать
        # код ошибки потока, и GetLastError вернёт не то, что вернул CreateMutexW.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = kernel32.CreateMutexW(None, False, "Local\\JarvisAssistantSingleInstance")
        _single_instance.handle = h                    # держим ссылку, пока процесс жив
        return ctypes.get_last_error() != 183          # 183 = ERROR_ALREADY_EXISTS
    except Exception:
        return True
from core import startup as _startup
_startup.mark("процесс запущен")
try:
    from core import icon as _icon
    _icon.set_app_id()                      # значок Джарвиса на панели задач вместо значка Python
except Exception:
    _icon = None
from core.logsetup import get as _getlog, setup as _setup_logs
_setup_logs()
log = _getlog("core")

import webview                              # окно нужно сразу
from core.lazy import Lazy
# тяжёлые библиотеки грузятся при первом обращении, а не при старте
psutil = Lazy("psutil")
requests = Lazy("requests")
sd = Lazy("sounddevice")
vosk = Lazy("vosk")
knowledge = Lazy("knowledge")
writer = Lazy("writer")
from llm import Ollama
import steam
import stt
from user_profile import Profile
from core import nlu
from core import asr_fix
from core.responder import Responder
from core.tts_norm import normalize_for_tts
from core.registry import Ctx
from core.actions import register_all
from core.browser import Browser
from core.store import CommandStore, _legacy_step
from core.scenario import ScenarioEngine
from core.wizard import Wizard
from core.content import Content
from core.paths import load_json as _load_cfg_json
from speech import (XttsSpeaker, make_fallback, make_speaker, speech_clean, split_sentences,
                    use_xtts)

_vosk_ready = [False]


def vosk_api():
    """vosk загружается лениво; уровень логов выставляется один раз."""
    if not _vosk_ready[0]:
        vosk.SetLogLevel(-1)
        _vosk_ready[0] = True
    return vosk

PLUGINS_DIR = os.path.join(BASE_DIR, "plugins")
MODEL_PATH = os.path.join(BASE_DIR, "model")
UI_PATH = os.path.join(BASE_DIR, "ui", "index.html")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
CUSTOM_PATH = os.path.join(BASE_DIR, "custom_commands.json")
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
PROFILE_PATH = os.path.join(BASE_DIR, "profile.json")
RU_KEYS = ("ru", "russian", "irina", "pavel")
SAMPLE_RATE = 16000
FRAMELESS = True            # False, если окно без рамки ведёт себя странно
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DEFAULT_CONFIG = {
    "ollama_model": "qwen2.5:3b",
    "ollama_url": "http://localhost:11434/api/chat",
    "wake_words": ["джарвис", "жарвис", "джарвиз", "джервис", "дарвис", "чарвис", "жервис", "джервиз"],
    "voice_rate": 180,
    "autostart": True,        # true: слушать сразу при запуске окна
    "stt_engine": "auto",         # auto | whisper | vosk: чем распознавать речь (Whisper точнее, ставится Install_Whisper.bat)
    "whisper_model": "auto",      # auto: на видеокарте large-v3-turbo, на процессоре small
    "whisper_engine": "auto",     # auto | faster | transformers: faster (быстрее) или transformers (как раньше)
    "asr_language": "auto",       # auto | ru | en | ru-en: язык распознавания (auto и ru-en понимают смешанную речь)
    "asr_second_pass": True,      # при неуверенности модели делать второй проход (ru и en) и брать лучший вариант
    "asr_fix_aliases": True,      # исправлять частые ошибки распознавания названий (config/asr_corrections.json)
    "whisper_start_wait": 6,      # сколько секунд ждать точное распознавание при старте (0 = слушать сразу на запасном)
    "mic_sensitivity": 3,         # 1-5: выше = лучше ловит тихую речь и шёпот (но и больше шума)
    "mic_boost": True,            # автоусиление тихого голоса для запасного распознавания Vosk
    "writer_model": "",           # модель для длинных текстов (пусто = как ollama_model)
    "auto_start_ollama": True,    # запускать Ollama вместе с Джарвисом
    "stop_ollama_on_exit": True,  # закрывать Ollama вместе с Джарвисом (если запустили мы)
    "auto_pull_model": True,      # скачивать модель автоматически
    "address_as": "",             # обращение (по умолчанию на «ты», без «сэр»)
    "web_search": "auto",         # auto | always | never
    "city": "",                   # город для «какая погода» без названия города
    "essay_sections": 4,          # сколько основных глав в реферате
    "keep_apps": ["steam.exe", "discord.exe", "telegram.exe"],   # эти программы Джарвис не закрывает при очистке окон и оптимизации
    "scenes": {
        "игровой режим": {
            "phrases": ["игровой режим", "режим игры", "включи игровой режим", "запусти игровой режим"],
            "steps": [
                {"do": "minimize_all"},
                {"do": "open_uri", "uri": "steam://open/main"},
                {"do": "say", "text": "Игровой режим включён."},
                {"do": "ask_game", "prompt": "Во что будем играть?"}
            ]
        }
    },
    "music_source": "youtube",    # youtube | yandex | vk | spotify: где искать музыку по умолчанию
    "music_app": "",              # программа для музыки (путь или имя); пусто = Spotify, если есть
    "music_default_query": "хорошая музыка для настроения",   # что включать на «включи музыку» без программы
    "tts_engine": "auto",         # auto | xtts | piper | sapi
    "xtts_voice": "",             # имя .wav из папки voices_xtts (пусто = первый по алфавиту)
    "piper_voice": "ru_RU-ruslan-medium",
    "piper_noise_scale": 0.75,
    "piper_noise_w": 0.9,
    "browser": "auto",            # auto | chrome | edge: браузер, которым Джарвис управляет через CDP
    "browser_port": 9222,
    "fuzzy_threshold": 80,        # порог нечёткого сопоставления команд (0-100)
    "apps": {},               # свои программы: "игра": "C:\\Games\\game.exe"
    "sites": {},              # свои сайты: "новости": "https://news.ycombinator.com"
}

SITES = {
    "ютуб": "https://www.youtube.com", "youtube": "https://www.youtube.com",
    "гугл": "https://www.google.com", "браузер": "https://www.google.com",
    "почту": "https://mail.google.com", "почта": "https://mail.google.com",
    "вконтакте": "https://vk.com", "вк": "https://vk.com",
    "яндекс": "https://ya.ru", "википедию": "https://ru.wikipedia.org",
    "википедия": "https://ru.wikipedia.org", "гитхаб": "https://github.com",
    # --- российские сервисы ---
    "яндекс почту": "https://mail.yandex.ru", "яндекс почта": "https://mail.yandex.ru",
    "яндекс диск": "https://disk.yandex.ru", "яндекс карты": "https://yandex.ru/maps",
    "карты": "https://yandex.ru/maps", "яндекс переводчик": "https://translate.yandex.ru",
    "переводчик": "https://translate.yandex.ru", "яндекс музыку": "https://music.yandex.ru",
    "яндекс музыка": "https://music.yandex.ru", "яндекс маркет": "https://market.yandex.ru",
    "яндекс еду": "https://eda.yandex.ru", "яндекс еда": "https://eda.yandex.ru",
    "такси": "https://3.redirect.appmetrica.yandex.com/route?start-lat=&start-lon=&appmetrica_tracking_id=1178268795219780156",
    "яндекс погоду": "https://yandex.ru/pogoda", "яндекс игры": "https://yandex.ru/games",
    "алису": "https://alice.yandex.ru", "алиса": "https://alice.yandex.ru",
    "кинопоиск": "https://www.kinopoisk.ru", "дзен": "https://dzen.ru",
    "авито": "https://www.avito.ru", "озон": "https://www.ozon.ru", "ozon": "https://www.ozon.ru",
    "вайлдберриз": "https://www.wildberries.ru", "вайлдберрис": "https://www.wildberries.ru",
    "вб": "https://www.wildberries.ru", "госуслуги": "https://www.gosuslugi.ru",
    "2гис": "https://2gis.ru", "два гис": "https://2gis.ru", "одноклассники": "https://ok.ru",
    "рутуб": "https://rutube.ru", "вк музыку": "https://vk.com/audio", "вк музыка": "https://vk.com/audio",
    "вк видео": "https://vkvideo.ru", "почту майл": "https://e.mail.ru", "майл": "https://mail.ru",
    "хедхантер": "https://hh.ru", "хх": "https://hh.ru", "сбербанк": "https://online.sberbank.ru",
    "сбер": "https://online.sberbank.ru", "тинькофф": "https://www.tbank.ru", "т банк": "https://www.tbank.ru",
    "ржд": "https://www.rzd.ru", "окко": "https://okko.tv", "винк": "https://wink.ru",
    "пикабу": "https://pikabu.ru", "хабр": "https://habr.com",
}

# эти названия сначала ищем среди установленных программ, и только потом открываем сайт
DUAL_APPS = {"яндекс музыку": "яндекс музыка", "яндекс музыка": "яндекс музыка"}

# «найди на авито кроссовки»: (фразы, адрес поиска, как произнести)
SEARCH_SITES = [
    (["найди в яндексе", "поищи в яндексе", "загугли в яндексе"], "https://yandex.ru/search/?text={q}", "в Яндексе"),
    (["найди на авито", "поищи на авито"], "https://www.avito.ru/rossiya?q={q}", "на Авито"),
    (["найди на озоне", "найди на озон", "поищи на озоне"], "https://www.ozon.ru/search/?text={q}", "на Озоне"),
    (["найди на вайлдберриз", "найди на вайлдберрис", "найди на вб", "поищи на вайлдберриз"],
     "https://www.wildberries.ru/catalog/0/search.aspx?search={q}", "на Вайлдберриз"),
    (["найди в кинопоиске", "найди фильм", "найди сериал", "поищи фильм"],
     "https://www.kinopoisk.ru/index.php?kp_query={q}", "на Кинопоиске"),
    (["найди на карте", "покажи на карте", "проложи маршрут до", "построй маршрут до", "как доехать до"],
     "https://yandex.ru/maps/?text={q}", "на карте"),
    (["найди в википедии", "поищи в википедии"], "https://ru.wikipedia.org/w/index.php?search={q}", "в Википедии"),
    (["найди на ютубе", "найди видео", "поищи на ютубе"], "https://www.youtube.com/results?search_query={q}", "на ютубе"),
    (["найди вакансии", "найди работу", "поищи вакансии"], "https://hh.ru/search/vacancy?text={q}", "на hh"),
    (["найди во вконтакте", "найди в вк", "найди человека"], "https://vk.com/search?c%5Bq%5D={q}", "во ВКонтакте"),
    (["переведи"], "https://translate.yandex.ru/?text={q}", "в переводчике"),
]

# (названия, что запускать, имена процессов для закрытия)
APPS_BUILTIN = [
    (["блокнот", "notepad"], "notepad.exe", ["notepad.exe"]),
    (["калькулятор"], "calc.exe", ["calculatorapp.exe", "calculator.exe", "calc.exe"]),
    (["проводник"], "explorer.exe", []),
    (["пейнт", "paint"], "mspaint.exe", ["mspaint.exe"]),
    (["диспетчер задач"], "taskmgr.exe", ["taskmgr.exe"]),
    (["командная строка", "командную строку", "консоль"], "cmd.exe", ["cmd.exe"]),
    (["настройки", "параметры"], "ms-settings:", []),
]

# как люди называют программы -> как они называются в меню Пуск
ALIASES = {
    "гугл хром": "chrome", "хром": "chrome", "фаерфокс": "firefox", "файрфокс": "firefox",
    "эдж": "edge", "опера": "opera", "яндекс браузер": "yandex", "стим": "steam",
    "эпик геймс": "epic games", "дискорд": "discord", "телеграм": "telegram",
    "телеграмм": "telegram", "телега": "telegram", "спотифай": "spotify", "ворд": "word",
    "эксель": "excel", "пауэрпоинт": "powerpoint", "павер поинт": "powerpoint",
    "вс код": "visual studio code", "код": "visual studio code", "обс": "obs studio",
    "вайбер": "viber", "скайп": "skype", "зум": "zoom", "вацап": "whatsapp",
    "ватсап": "whatsapp", "майнкрафт": "minecraft", "оллама": "ollama",
    "яндекс музыка": "яндекс музыка", "яндекс музыку": "яндекс музыка", "яндекс диск": "яндекс диск",
    "вк мессенджер": "vk messenger", "вк тим": "vk teams", "макс": "max", "мессенджер макс": "max",
    "яндекс телемост": "телемост", "телемост": "телемост", "мой офис": "мойофис",
    "сбер салют": "салют", "алиса": "алиса",
}

try:
    for _k, _v in ((_load_cfg_json("app_aliases.json", {}) or {}).get("aliases") or {}).items():
        ALIASES[nlu.norm(_k)] = _v
except Exception:
    pass

FILLER = {"пожалуйста", "программу", "программа", "приложение", "игру", "мне", "окно", "джарвис"}
SKIP_WORDS = ("uninstall", "удалить", "readme", "справка", "help", "website", "сайт", "license")
GENERIC_EXES = {"update.exe", "launcher.exe", "setup.exe", "start.exe", "app.exe"}
PROTECTED = {
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "svchost.exe", "explorer.exe", "dwm.exe", "fontdrvhost.exe", "python.exe",
    "pythonw.exe", "ctfmon.exe", "msedgewebview2.exe", "searchhost.exe", "sihost.exe",
    "startmenuexperiencehost.exe", "shellexperiencehost.exe", "taskhostw.exe", "runtimebroker.exe",
}

VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAD, 0xAE, 0xAF
VK_MEDIA_NEXT, VK_MEDIA_PREV, VK_MEDIA_PLAY = 0xB0, 0xB1, 0xB3
VK_LWIN, VK_D = 0x5B, 0x44

NUM_WORDS = {
    "ноль": 0, "один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
    "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100,
}

_TR = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
     "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


DOCS_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Jarvis")
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
          "октября", "ноября", "декабря"]

# Несколько вариантов на каждый случай: Джарвис выбирает случайный, чтобы не повторяться
PHRASES = {
    "start": ["Системы запущены. Слушаю вас.", "Я на связи.", "Всё готово. К вашим услугам.",
              "На месте. Чем могу быть полезен?", "Доброго времени суток. Слушаю."],
    "ack": ["Готово.", "Выполнено.", "Сделано.", "Пожалуйста.", "Как скажете."],
    "open": ["Открываю {x}.", "Запускаю {x}.", "Секунду, открываю {x}.", "Уже открываю {x}."],
    "close": ["Закрываю {x}.", "Завершаю {x}.", "Хорошо, закрываю {x}."],
    "louder": ["Погромче.", "Прибавляю звук.", "Сделал громче."],
    "quieter": ["Потише.", "Убавляю звук.", "Сделал тише."],
    "bye": ["До встречи.", "Ухожу в режим ожидания. Зовите, если что.", "Отдыхаю. Я рядом."],
    "search": ["Ищу {x}.", "Уже ищу: {x}.", "Секунду, поищу {x}."],
    "timer": ["Таймер на {x} запущен.", "Засёк: {x}.", "Хорошо, засекаю {x}."],
    "shot": ["Скриншот сохранён в папке «Изображения».", "Снимок экрана готов, он в папке «Изображения»."],
    "write_start": ["Принято: {kind} на тему «{topic}». Приступаю, скажу, когда закончу.",
                    "Хорошо, берусь за работу: {kind}, тема «{topic}». Это займёт несколько минут.",
                    "Задача понятна: {kind} на тему «{topic}». Дам знать, когда будет готово."],
    "write_done": ["Готово. Документ сохранён в папке «Документы» и уже открыт.",
                   "Закончил. Файл лежит в папке «Документы», я его открыл.",
                   "Работа готова. Открыл её для вас."],
    "time": ["Сейчас {h} {hw} {m} {mw}.", "На часах {h} {hw} {m} {mw}.", "Время: {h} {hw} {m} {mw}."],
    "time0": ["Сейчас ровно {h} {hw}.", "На часах ровно {h} {hw}.", "Время: ровно {h} {hw}."],
    "date": ["Сегодня {wd}, {d} {mo} {y} года.", "Сегодня {d} {mo}, {wd}.", "{wd}, {d} {mo} {y} года."],
}
PH_ADDR = [""]          # обращение к пользователю (заполняется из config.json)
PH_ADDR_KEYS = {"ack", "open", "close", "louder", "quieter", "search", "timer", "shot"}


RESP = Responder(fallback=PHRASES)


def ph(key, **kw):
    """Живая фраза: шаблоны из config/responses.json, без повторов последних 20, с учётом контекста."""
    try:
        return RESP.say(key, **kw)
    except KeyError:
        return random.choice(PHRASES[key]).format(**kw)


# ---------------- Вспомогательные функции ----------------
def norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower().replace("ё", "е")).split())


def translit(s: str) -> str:
    return "".join(_TR.get(ch, ch) for ch in s.lower())


def similarity(q: str, k: str) -> float:
    if not q or not k:
        return 0.0
    if q == k:
        return 1.0
    s = difflib.SequenceMatcher(None, q, k).ratio()
    if len(q) >= 3 and k.startswith(q):
        s = max(s, 0.9)
    return s


def tap_key(vk, *mods):
    u = ctypes.windll.user32
    for m in mods:
        u.keybd_event(m, 0, 0, 0)
    u.keybd_event(vk, 0, 0, 0)
    u.keybd_event(vk, 0, 2, 0)
    for m in reversed(mods):
        u.keybd_event(m, 0, 2, 0)


def plural(n, forms):
    n = abs(n) % 100
    if 11 <= n <= 19:
        return forms[2]
    n %= 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]


def parse_duration(text: str) -> int:
    words = text.split()
    if "полчаса" in words:
        return 1800
    total, found = 0, False
    for w in words:
        if w.isdigit():
            total += int(w)
            found = True
        elif w in NUM_WORDS:
            total += NUM_WORDS[w]
            found = True
    mult = 60
    if any(w.startswith("секунд") for w in words):
        mult = 1
    elif any(w.startswith("час") for w in words):
        mult = 3600
    if not found:
        if any(w in ("минуту", "секунду", "час") for w in words):
            total = 1
        else:
            return 0
    return total * mult


def fmt_duration(sec: int) -> str:
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    parts = []
    if h:
        parts.append(f"{h} {plural(h, ('час', 'часа', 'часов'))}")
    if m:
        parts.append(f"{m} {plural(m, ('минуту', 'минуты', 'минут'))}")
    if s:
        parts.append(f"{s} {plural(s, ('секунду', 'секунды', 'секунд'))}")
    return " ".join(parts)


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH, encoding="utf-8-sig") as f:
            user = json.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except FileNotFoundError:
        pass
    except Exception:
        traceback.print_exc()
    return cfg


# ---------------- Индекс программ из меню Пуск ----------------
class AppIndex:
    def __init__(self):
        self.entries = []
        self.ready = False

    def build(self):
        dirs = [
            os.path.join(os.environ.get("ProgramData", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
            os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
        ]
        files = []
        for d in dirs:
            for root, _, names in os.walk(d):
                for f in names:
                    if f.lower().endswith(".lnk"):
                        files.append(os.path.join(root, f))
        shell = None
        try:
            import comtypes
            import comtypes.client
            comtypes.CoInitialize()
            try:
                shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
            except TypeError:
                shell = comtypes.client.CreateObject("WScript.Shell")
        except Exception:
            shell = None
        entries = []
        for path in files:
            name = os.path.splitext(os.path.basename(path))[0]
            low = name.lower()
            if any(bad in low for bad in SKIP_WORDS):
                continue
            exe = None
            if shell is not None:
                try:
                    exe = os.path.basename(shell.CreateShortCut(path).TargetPath).lower()
                except Exception:
                    exe = None
            if not exe or not exe.endswith(".exe"):
                exe = None
            keys = [low] + [t for t in re.split(r"[\s\-_.]+", low) if len(t) >= 3]
            entries.append({"name": name, "path": path, "exe": exe, "keys": keys})
        self.entries = entries
        self.ready = True

    def find(self, query: str):
        qs = {query.lower(), translit(query)}
        best, best_s = None, 0.0
        for e in self.entries:
            for k in e["keys"]:
                s = max(similarity(q, k) for q in qs)
                if s > best_s:
                    best, best_s = e, s
        return best if best_s >= 0.72 else None


class Command:
    def __init__(self, triggers, run, source="встроенная"):
        self.triggers = [norm(t) for t in triggers]
        self.run = run
        self.source = source


# ---------------- Ядро ----------------
class Core:
    def __init__(self):
        self.cfg = load_config()
        self.events = queue.Queue()
        self.cmd_q = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False
        self.level = 0.0
        self.state = "off"
        self.awake_until = 0.0
        self.model = None
        self.engine = None
        self.index = AppIndex()
        self.custom_items = []    # свои команды из вкладки «Команды»
        self.custom = []
        self.settings = {}
        self.speaker = None       # создаётся в bootstrap
        self.commands = []        # команды из плагинов
        self.plugin_info = []
        self.base_dir = BASE_DIR
        self.wake_words = [norm(w) for w in self.cfg["wake_words"]]
        self.history = []         # только реплики пользователя и Джарвиса
        self.ollama = Ollama(self.cfg)
        self.llm_ready = threading.Event()
        self.llm_error = ""
        self.writing = False
        self.write_cancel = False
        self.profile = Profile(PROFILE_PATH)
        self.t_start = time.time()
        self.whisper = None
        self.whisper_failed = False
        self.stt_ready = threading.Event()
        self.pending = None          # (функция, время устаревания): ждём ответ на вопрос Джарвиса
        self.last_raw = ""
        PH_ADDR[0] = (self.cfg.get("address_as") or "").strip()
        self.responder = RESP
        self.browser = Browser(self.cfg)
        self.store = CommandStore()
        self.registry = register_all(self)            # создаёт и self.safety
        self.scenarios = ScenarioEngine(self)
        self.wizard = Wizard(self)
        self.content = Content(self)
        self.last_found = []
        self.ready = {"ears": False, "voice": False, "brain": False, "apps": False}
        self._store_check = 0.0
        self.builtin = [
            Command(["оптимизируй компьютер", "оптимизируй пк", "оптимизация компьютера", "ускорь компьютер",
                     "почисти компьютер", "освободи память", "очисти память"], self.c_optimize),
            Command(["добавь несколько команд", "создай несколько команд", "добавь команды"],
                    lambda a, jj=None: self.wizard.start_batch()),
            Command(["добавь команду", "создай команду", "создай плагин", "добавь плагин", "мастер команд",
                     "новая команда", "добавить команду"], lambda a, jj=None: self.wizard.start()),
            Command(["мои команды", "список команд", "какие у меня команды", "покажи мои команды"],
                    lambda a, jj=None: self.wizard.list_text()),
            Command(["удали команду"], lambda a, jj=None: self.wizard.delete(a)),
            Command(["измени команду", "поменяй команду", "отредактируй команду"], lambda a, jj=None: self.wizard.edit(a)),
            Command(["покажи шаги команды", "шаги команды"], lambda a, jj=None: self.wizard.show(a)),
            Command(["экспортируй команды", "экспорт команд"], self.c_export_cmds),
            Command(["импортируй команды", "импорт команд"], self.c_import_cmds),
            Command(["обнови команды", "перезагрузи команды"], self.c_reload_cmds),
            Command(["сохрани как команду"], self.c_save_adhoc),
            Command(["пошути", "расскажи шутку", "расскажи анекдот", "рассмеши меня", "повесели меня"],
                    lambda a, jj=None: self.content.joke(a)),
            Command(["интересный факт", "расскажи факт", "удиви меня", "расскажи что нибудь интересное",
                     "что нибудь интересное"], lambda a, jj=None: self.content.fact(a)),
            Command(["загадай загадку", "загадай мне загадку"], lambda a, jj=None: self.content.riddle()),
            Command(["расскажи историю", "расскажи страшилку", "расскажи что нибудь страшное", "мотивируй меня",
                     "расскажи сказку", "расскажи мотивацию"], lambda a, jj=None: self.content.story(self.last_raw)),
            Command(["поиграем", "давай поиграем", "сыграем", "хочу играть", "давай сыграем"],
                    lambda a, jj=None: self.content.menu()),
            Command(["угадай число"], lambda a, jj=None: self.content.guess_number()),
            Command(["камень ножницы бумага"], lambda a, jj=None: self.content.rps()),
            Command(["орел или решка", "подбрось монетку", "брось монетку"], lambda a, jj=None: self.content.coin()),
            Command(["игра в слова", "сыграем в слова"], lambda a, jj=None: self.content.word_game()),
            Command(["запомни"], self.c_remember),
            Command(["что ты обо мне знаешь", "что ты знаешь обо мне", "что ты помнишь обо мне",
                     "что ты обо мне помнишь", "что ты помнишь про меня"], self.c_what_know),
            Command(["забудь"], self.c_forget),
            Command(["выключись", "до свидания", "отдыхай", "отключись"], self.c_stop),
            Command(["который час", "который сейчас час", "сколько времени", "сколько сейчас времени", "сколько сейчас время",
                     "сколько время", "сколько на часах", "текущее время", "скажи время"], self.c_time),
            Command(["какое число", "какое сегодня число", "какая дата", "какая сегодня дата", "какой сегодня день",
                     "какой день недели"], self.c_date),
            *[Command(trigs, lambda a, jj=None, url=url, lab=lab: self.c_site_search(url, lab, a))
              for trigs, url, lab in SEARCH_SITES],
            Command(["найди", "поищи", "загугли", "погугли"], self.c_search),
            Command(["включи музыку", "поставь музыку", "запусти музыку", "включи песню", "поставь песню",
                     "включи трек", "поставь трек", "сыграй", "включи что нибудь"], self.c_music),
            Command(["запусти игру", "включи игру", "поиграем в", "хочу поиграть в"], self.c_game),
            Command(["открой", "запусти", "открыть"], self.c_open),
            Command(["закрой", "закрыть", "завершить программу"], self.c_close),
            Command(["громче", "прибавь звук", "увеличь громкость"], self.c_vol_up),
            Command(["тише", "убавь звук", "уменьши громкость"], self.c_vol_down),
            Command(["выключи звук", "без звука", "отключи звук", "включи звук"], self.c_mute),
            Command(["пауза", "поставь на паузу", "продолжи", "продолжай", "останови музыку"], self.c_play),
            Command(["следующий трек", "следующая песня", "следующую песню", "переключи трек"], self.c_next),
            Command(["предыдущий трек", "предыдущая песня", "прошлый трек"], self.c_prev),
            Command(["скриншот", "снимок экрана"], self.c_screenshot),
            Command(["заблокируй"], self.c_lock),
            Command(["сверни все", "сверни окна", "покажи рабочий стол"], self.c_desktop),
            Command(["выключи компьютер", "выключи пк", "заверши работу"], self.c_shutdown),
            Command(["перезагрузи компьютер", "перезагрузи пк"], self.c_restart),
            Command(["отмени выключение", "отмени перезагрузку", "отмена выключения"], self.c_cancel_shutdown),
            Command(["состояние системы", "системная информация", "загрузка процессора", "заряд батареи"], self.c_sysinfo),
            Command(["таймер"], self.c_timer),
            Command(["обнови плагины", "перезагрузи плагины"], self.c_reload),
            Command(["презентацию", "презентация", "презентацией", "слайды"],
                    lambda a, jj=None: self.c_make_file("pptx", a)),
            Command(["таблицу", "таблица", "эксель", "экселе", "excel"],
                    lambda a, jj=None: self.c_make_file("xlsx", a)),
            Command(["напиши", "сочини", "составь", "подготовь"], self.c_write),
            Command(["отмени написание", "прекрати писать", "хватит писать"], self.c_write_cancel),
            Command(["погод", "сколько градусов", "температура на улице"], self.c_weather),
            Command(["что ты умеешь", "помощь"], self.c_help),
        ]

        self.load_settings()
        self.load_custom()

    # ----- события для интерфейса -----
    def emit(self, kind, **kw):
        self.events.put({"t": kind, **kw})

    def set_state(self, name):
        self.state = name
        self.emit("state", v=name)

    def poll(self):
        if time.time() - self._store_check > 1.0:
            self._store_check = time.time()
            try:
                if self.store.changed_on_disk():
                    self.reload_custom()
            except Exception:
                log.exception("горячая перезагрузка команд")
        evs = []
        try:
            while len(evs) < 100:
                evs.append(self.events.get_nowait())
        except queue.Empty:
            pass
        return {"events": evs, "level": self.level}

    def info(self):
        return {
            "plugins": self.plugin_info,
            "running": self.running,
            "model_ok": os.path.isdir(MODEL_PATH),
            "apps": len(self.index.entries),
            "frameless": FRAMELESS,
        }

    # ----- запуск фоновых задач -----
    def stage(self, name):
        """Время от запуска до каждого этапа пишется в logs/jarvis.log (строки «[старт]»)."""
        _startup.mark(name)
        key = ("voice" if ("голос" in name.lower() or "piper" in name.lower()) else
               "ears" if ("vosk" in name.lower() or "whisper" in name.lower()) else
               "brain" if "нейросет" in name.lower() or "ollama" in name.lower() else
               "apps" if "программ" in name.lower() else None)
        if key:
            self.ready[key] = True
            self.emit("ready", **self.ready)

    def bootstrap(self):
        nlu.warm_up()                                      # лемматизатор грузится в фоне, команды не ждут
        self.speaker = make_speaker(self, fast=True)      # Piper или Windows: готовы за секунду
        self.stage("голос готов")
        threading.Thread(target=self._start_llm, daemon=True).start()
        threading.Thread(target=self._announce_voice, daemon=True).start()
        threading.Thread(target=self._preload_stt, daemon=True).start()
        if use_xtts(self.cfg):
            threading.Thread(target=self._upgrade_voice, daemon=True).start()
        self.load_plugins()
        try:
            self.index.build()
        except Exception:
            traceback.print_exc()
        self.stage("список программ готов")

    def _preload_stt(self):
        """Модели распознавания грузятся заранее и параллельно, чтобы к нажатию «Включить» всё было готово."""
        mode = str(self.cfg.get("stt_engine", "auto")).lower()
        use_whisper = mode in ("auto", "whisper") and stt.whisper_installed()
        if use_whisper:
            threading.Thread(target=self._load_whisper, daemon=True).start()
        if not (use_whisper and mode == "whisper") and os.path.isdir(MODEL_PATH) and self.model is None:
            try:
                self.model = vosk_api().Model(MODEL_PATH)
                self.stage("Vosk загружен")
            except Exception:
                traceback.print_exc()
        self.stt_ready.set()

    def _load_whisper(self):
        try:
            c = stt.WhisperClient(self.cfg.get("whisper_model", "auto"))
            c.start()
            self.whisper = c
            self.stage(f"Whisper загружен ({c.device})")
        except Exception:
            traceback.print_exc()
            self.whisper_failed = True
            self.emit("msg", who="err", text="Whisper не запустился, использую обычное распознавание. "
                                              "Подробности в файле whisper.log.")

    def _upgrade_voice(self):
        """Клон голоса грузится долго: пока он грузится, говорит обычный голос, потом подменяем."""
        x = XttsSpeaker(self)
        x.ready.wait(timeout=600)
        if x.ok:
            old, self.speaker = self.speaker, x
            try:
                old.stop()
            except Exception:
                pass
            self.stage("клон голоса готов")
            self.emit("msg", who="sys", text="Голос: клон голоса (XTTS).")
            self.emit("voices")
        else:
            x.close()
            self.emit("msg", who="err", text="Клон голоса не запустился, остаюсь на обычном голосе. "
                                              "Подробности в файлах logs/jarvis.log и xtts.log.")

    def _start_llm(self):
        models = [self.cfg["ollama_model"]]
        wm = self.cfg.get("writer_model")
        if wm and wm not in models:
            models.append(wm)
        try:
            err = self.ollama.ensure(lambda kind, text: self.emit("msg", who=kind, text=text), models)
            self.llm_error = err or ""
        except Exception as e:
            traceback.print_exc()
            self.llm_error = "Не получилось подготовить нейросеть. Подробности в файле logs/jarvis.log."
            self.emit("msg", who="err", text="Ошибка Ollama: " + str(e))
        finally:
            self.llm_ready.set()
            self.stage("нейросеть готова")

    def _announce_voice(self):
        sp = self.speaker
        sp.ready.wait(timeout=40)
        if sp.engine == "piper" and not sp.ok:
            self.emit("msg", who="err", text="Нейросетевой голос не запустился, включаю голос Windows. "
                                              "Подробности в файле logs/jarvis.log.")
            if self.speaker is sp:
                self.speaker = make_fallback(self, "piper")
        elif sp.engine == "piper":
            self.emit("msg", who="sys", text="Голос: нейросетевой (Piper).")
        else:
            self.emit("msg", who="sys", text="Голос: Windows. Для живого голоса запустите INSTALL_JARVIS.bat.")
        self.stage("голос готов")

    def shutdown(self):
        self.stop_event.set()
        self.write_cancel = True
        try:
            if self.speaker is not None:
                self.speaker.stop()
                if hasattr(self.speaker, "close"):
                    self.speaker.close()
        except Exception:
            pass
        self.ollama.stop()
        if self.whisper is not None:
            self.whisper.close()

    def hush(self):
        if self.speaker is not None:
            self.speaker.stop()

    def load_plugins(self):
        self.commands = []
        info = []
        os.makedirs(PLUGINS_DIR, exist_ok=True)
        for fn in sorted(os.listdir(PLUGINS_DIR)):
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            path = os.path.join(PLUGINS_DIR, fn)
            try:
                spec = importlib.util.spec_from_file_location("jarvis_plugin_" + fn[:-3], path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                name = getattr(mod, "NAME", fn[:-3])
                trig_all = []
                for c in getattr(mod, "COMMANDS", []):
                    self.commands.append(Command(c["triggers"], c["run"], name))
                    trig_all += c["triggers"]
                info.append({"name": name, "file": fn, "ok": True, "triggers": trig_all})
            except Exception as e:
                traceback.print_exc()
                info.append({"name": fn, "file": fn, "ok": False, "error": str(e), "triggers": []})
        self.plugin_info = info
        self.emit("plugins", list=info)
        return info

    def toggle(self):
        if self.running:
            self.stop_event.set()
            self.set_state("stopping")
        else:
            self.running = True
            self.stop_event.clear()
            while not self.cmd_q.empty():
                self.cmd_q.get_nowait()
            threading.Thread(target=self._worker, daemon=True).start()

    def send_text(self, text):
        text = (text or "").strip()
        if not text:
            return
        if not self.running:
            self.emit("msg", who="sys", text="Сначала нажмите «Включить».")
            return
        self.cmd_q.put(("text", text))

    # ----- голос и ИИ -----
    @staticmethod
    def _speech_part(text, max_sent=6, max_chars=700):
        """Что именно произнести вслух: длинные ответы читаем не целиком, остальное остаётся в чате."""
        out, n = [], 0
        parts = split_sentences(text)
        for p in parts:
            if len(out) >= max_sent or n + len(p) > max_chars:
                break
            out.append(p)
            n += len(p)
        if not out and parts:
            out = [parts[0][:max_chars]]
        return normalize_for_tts(speech_clean(" ".join(out)))

    def say(self, text, max_sent=6, max_chars=700):
        self.emit("msg", who="jarvis", text=text)
        spoken = self._speech_part(text, max_sent, max_chars)
        if spoken and self.speaker is not None and self.speaker.ok:
            self.set_state("speaking")
            try:
                self.speaker.speak(spoken)
            except Exception:
                traceback.print_exc()
        self.set_state("listening")

    # ----- нейросеть -----
    def system_prompt(self):
        now = datetime.datetime.now()
        addr = (self.cfg.get("address_as") or "").strip()
        return (
            "Ты Джарвис, личный ИИ-ассистент в духе дворецкого Тони Старка: вежливый, находчивый, "
            "с лёгкой сухой иронией, без панибратства. "
            + (f"К пользователю обращайся «{addr}», но не в каждой фразе. " if addr else "")
            + f"Сегодня {WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]} {now.year} года, "
              f"сейчас {now:%H:%M}. "
              "Говори по-русски живой разговорной речью, каждый раз по-новому, без шаблонов и канцелярита. "
              "Ответ будет озвучен голосом, поэтому отвечай коротко: обычно одно-три предложения, максимум пять; "
              "без списков, таблиц, markdown, эмодзи и ссылок. "
              "Если не знаешь точно, так и скажи, не выдумывай факты и цифры. "
            + self.profile.prompt())

    def ask_ai(self, question):
        """Простой вопрос к нейросети с ответом строкой (удобно для плагинов)."""
        msgs = [{"role": "system", "content": self.system_prompt()}] + self.history[-8:] \
               + [{"role": "user", "content": question}]
        try:
            answer = self.ollama.chat(msgs, temperature=0.8)
        except Exception:
            return "Не могу связаться с нейросетью. Проверьте, что Ollama запущена."
        answer = re.sub(r"[*_`#]", "", answer)
        self.history += [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
        del self.history[:-12]
        return answer

    def wait_llm(self):
        if not self.llm_ready.wait(timeout=3):
            self.say("Нейросеть ещё запускается. Подождите немного и повторите вопрос.")
            return False
        if self.llm_error:
            self.say(self.llm_error)
            return False
        return True

    def answer(self, q):
        """Любой вопрос, для которого нет готовой команды."""
        t = norm(q)
        quick = knowledge.currency_answer(t)           # курсы валют: быстро и точно, без нейросети
        if quick:
            self.say(quick)
            return
        if not self.wait_llm():
            return
        ctx = ""
        if knowledge.needs_web(t, self.cfg.get("web_search", "auto")):
            self.set_state("thinking")
            ctx = knowledge.web_context(q)
        self.chat_stream(q, ctx)

    def chat_stream(self, q, ctx=""):
        """Ответ нейросети: текст идёт в чат, а голос начинает говорить с первого предложения."""
        extra = ""
        if ctx:
            extra = ("\n\nДанные из интернета (могут быть неточными; используй только относящееся к вопросу, "
                     "а если точных данных нет, честно скажи об этом):\n" + ctx)
        msgs = [{"role": "system", "content": self.system_prompt() + extra}] + self.history[-8:] \
               + [{"role": "user", "content": q}]
        self.set_state("thinking")
        full, buf = "", ""
        st = {"n": 0, "chars": 0, "first": True}
        can_speak = self.speaker is not None and self.speaker.ok

        def piece(sentence):
            sentence = re.sub(r"[*_`#]", "", sentence).strip()
            if not sentence:
                return
            self.emit("msgpart", who="jarvis", text=("" if st["first"] else " ") + sentence, first=st["first"])
            st["first"] = False
            if can_speak and st["n"] < 6 and st["chars"] < 700:
                self.set_state("speaking")
                self.speaker.speak(normalize_for_tts(speech_clean(sentence)), wait=False)
                st["n"] += 1
                st["chars"] += len(sentence)

        try:
            for chunk in self.ollama.stream(msgs, temperature=0.8):
                full += chunk
                buf += chunk
                while True:
                    m = re.search(r"^(.*?[.!?…]+)\s+(?=\S)", buf, re.S)
                    if not m:
                        break
                    piece(m.group(1))
                    buf = buf[m.end():]
            piece(buf)
        except Exception:
            traceback.print_exc()
            if not full:
                self.say("Не могу связаться с нейросетью. Проверьте, что Ollama запущена.")
                return
        if can_speak:
            self.speaker.wait_idle(timeout=120)
        self.set_state("listening")
        clean = re.sub(r"[*_`#]", "", full).strip()
        self.history += [{"role": "user", "content": q}, {"role": "assistant", "content": clean}]
        del self.history[:-12]

    # ----- погода -----
    def c_weather(self, args, j=None):
        full = norm(self.last_raw)
        day = 1 if "завтра" in full else 0
        city, conf, cands = nlu.places().find(full)
        if city is None:
            city = (self.cfg.get("city") or "").strip()
            if not city:
                return "В каком городе? Скажи, например: погода в Москве. Или впиши свой город в config.json."
        elif conf < 0.9:                                           # не подставляем город наугад
            self.ask_yes_no(f"Ты про город {city}?", lambda: self._weather_say(city, day))
            return ""
        return self._weather_for(city, day)

    def _weather_say(self, city, day):
        return self._weather_for(city, day)

    def _weather_for(self, city, day):
        try:
            res = knowledge.weather_answer(city, day)
        except Exception:
            log.exception("погода")
            return "Не получилось узнать погоду. Проверь интернет."
        return res or f"Не нашёл город {city}."

    # ----- написание текстов -----
    def c_write(self, args, j=None):
        if self.writing:
            return "Я ещё пишу предыдущую работу. Скажите: отмени написание, чтобы остановить."
        spec, topic = writer.detect(args)
        if spec is None:
            return "Что написать? Например: напиши реферат на тему архитектура школы."
        self.writing, self.write_cancel = True, False
        threading.Thread(target=self._write_job, args=(spec, topic), daemon=True).start()
        return ph("write_start", kind=spec[1], topic=topic)

    def c_write_cancel(self, args, j=None):
        if not self.writing:
            return "Я сейчас ничего не пишу."
        self.write_cancel = True
        return "Хорошо, остановлюсь после текущего раздела."

    def _write_job(self, spec, topic):
        label, multi = spec[1], spec[2]
        try:
            if not self.llm_ready.wait(timeout=900) or self.llm_error:
                self.cmd_q.put(("say", self.llm_error or "Нейросеть не готова."))
                return
            model = self.cfg.get("writer_model") or self.cfg["ollama_model"]

            def ask(system, user, temp, num_predict, timeout):
                return self.ollama.chat(
                    [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    model=model, temperature=temp, num_predict=num_predict, num_ctx=4096, timeout=timeout)

            parts = writer.generate(ask, spec, topic, int(self.cfg.get("essay_sections", 4)),
                                    lambda t: self.emit("msg", who="sys", text=t), lambda: self.write_cancel)
            if parts is None:
                self.cmd_q.put(("say", "Хорошо, остановился."))
                return
            path = writer.save_document(label, topic, parts, DOCS_DIR)
            self.emit("msg", who="sys", text="Файл сохранён: " + path)
            words = sum(len(t.split()) for _, t in parts)
            if multi or words > 200:
                try:
                    os.startfile(path)
                except Exception:
                    pass
                self.cmd_q.put(("say", ph("write_done")))
            else:
                self.cmd_q.put(("read", parts[0][1]))      # короткие тексты читаем вслух
        except Exception as e:
            traceback.print_exc()
            self.emit("msg", who="err", text="Не получилось дописать работу: " + str(e))
            self.cmd_q.put(("say", "Не получилось закончить работу. Подробности в файле logs/jarvis.log."))
        finally:
            self.writing = False

    # ----- вопросы и подтверждения -----
    def ask(self, question, callback, seconds=25, raw=False):
        """Задаёт вопрос голосом; следующая фраза пользователя пойдёт в callback(текст).
        raw=True: в callback попадают и слова «нет/отмена» (для вопросов да/нет и мастера)."""
        self.pending = (callback, time.time() + seconds, raw)
        self.cmd_q.put(("say", question))

    def confirm_blocking(self, question, timeout=None):
        """Блокирующее подтверждение голосом для фоновых потоков (сценарии). НЕ вызывать из потока распознавания."""
        timeout = timeout or self.safety.confirm_timeout
        ev, res = threading.Event(), {"v": False}
        yes = self.safety.yes

        def cb(raw):
            t = norm(raw)
            res["v"] = bool(t) and (t in yes or t.split()[0] in yes)
            ev.set()
            return ""
        self.pending = (cb, time.time() + timeout + 5, True)
        self.cmd_q.put(("say", self.ph("confirm_ask", x=question)))
        ev.wait(timeout + 5)
        if not ev.is_set():
            self.pending = None
        log.info("подтверждение «%s»: %s", question, res["v"])
        return res["v"]

    def ask_yes_no(self, question, on_yes):
        YES = {"да", "давай", "ага", "конечно", "угу", "закрывай", "делай", "подтверждаю", "валяй", "можно", "да да"} \
              | self.safety.yes

        def cb(raw):
            t = norm(raw)
            if t and (t in YES or t.split()[0] in YES):
                return on_yes() or ""
            return "Хорошо, ничего не трогаю."
        self.ask(question, cb, raw=True)

    # ----- сценарии («игровой режим» и свои) -----
    def c_scene(self, scene):
        threading.Thread(target=self._run_steps, args=(list(scene.get("steps") or []),), daemon=True).start()
        return ""

    def _say_async(self, text):
        self.cmd_q.put(("say", text))

    def _run_cmd(self, cmd):
        """Запуск программы из шага сценария. Строка разбирается на аргументы без shell,
        поэтому «;», «&&», «|» не имеют силы: config.json не может выполнить произвольную команду."""
        if not cmd:
            return
        self.safety.popen(cmd)

    def _run_steps(self, steps):
        for st in steps:
            do = st.get("do")
            try:
                if do == "say":
                    self._say_async(st.get("text", ""))
                elif do == "minimize_all":
                    tap_key(VK_D, VK_LWIN)
                elif do == "open":
                    self.c_open(norm(st.get("target", "")))
                elif do == "open_uri":
                    os.startfile(st["uri"])
                elif do == "close_apps":
                    for a in st.get("apps", []):
                        self.c_close(norm(a))
                elif do == "close_all":
                    self._close_all_windows(st.get("keep", []))
                elif do == "wait":
                    time.sleep(float(st.get("seconds", 1)))
                elif do == "music":
                    self.c_music(st.get("query", ""))
                elif do == "run":
                    # без shell=True: иначе подменённый config.json = выполнение произвольной команды
                    self._run_cmd(st["cmd"])
                elif do == "ask_game":
                    self.ask_game(st.get("prompt", "Во что будем играть?"))
                    return                       # дальше продолжит ответ пользователя
            except Exception:
                traceback.print_exc()

    def _visible_window_exes(self):
        """Имена программ, у которых сейчас есть видимые окна."""
        user32 = ctypes.windll.user32
        pids = set()
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                pids.add(pid.value)
            return True
        user32.EnumWindows(EnumProc(cb), 0)
        names = set()
        for pid in pids:
            try:
                if pid != os.getpid():
                    names.add(psutil.Process(pid).name().lower())
            except Exception:
                pass
        return names

    def _keep_set(self, extra=()):
        keep = {x.lower() for x in (self.cfg.get("keep_apps") or [])} | {x.lower() for x in extra}
        keep |= PROTECTED | {"ollama.exe", "ollama app.exe", "python.exe", "pythonw.exe", "jarvis.exe"} | self.MUSIC_PLAYERS
        return {k if k.endswith(".exe") or k == "system" else k + ".exe" for k in keep}

    def _close_all_windows(self, extra_keep=()):
        keep = self._keep_set(extra_keep)
        closed = []
        for name in self._visible_window_exes():
            if name in keep:
                continue
            r = subprocess.run(["taskkill", "/IM", name], capture_output=True, creationflags=NO_WINDOW)   # мягкое закрытие
            if r.returncode == 0:
                closed.append(name)
        return closed

    # ----- запуск игр -----
    def ask_game(self, prompt):
        tries = {"n": 0}

        def cb(raw):
            ok = self.launch_game(raw)
            if ok:
                return ok
            tries["n"] += 1
            if tries["n"] < 2:
                self.ask("Не нашёл такую игру. Повторите название.", cb)
                return ""
            return "Не нашёл такую игру. Скажите: запусти и название, когда определитесь."
        self.ask(prompt, cb)

    def launch_game(self, raw):
        """Ищет игру среди установленных в Steam, потом среди программ. Возвращает фразу или None."""
        words = [w for w in norm(raw).split() if w not in
                 {"запусти", "включи", "открой", "давай", "поиграем", "во", "в", "хочу", "играть", "поиграть",
                  "игру", "игра", "мне", "пожалуйста", "запускай"}]
        name = " ".join(words)
        if not name:
            return None
        games = {n: a for a, n in steam.installed_games()}
        best = self.best_match(name, list(games), cutoff=0.5) if games else None
        if best:
            os.startfile(f"steam://rungameid/{games[best]}")
            return f"Запускаю {best}."
        e = self.index.find(self._resolve_alias(name))
        if e:
            os.startfile(e["path"])
            return f"Запускаю {e['name']}."
        return None

    def c_game(self, args, j=None):
        r = self.launch_game(args)
        return r or "Не нашёл такую игру среди установленных."

    # ----- оптимизация компьютера -----
    def c_optimize(self, args, j=None):
        threading.Thread(target=self._optimize_scan, daemon=True).start()
        return "Анализирую компьютер."

    def _optimize_scan(self):
        keep = self._keep_set()
        me = os.getpid()
        total = {}
        for p in psutil.process_iter(["name", "memory_info", "pid"]):
            try:
                n = (p.info["name"] or "").lower()
                if not n or n in keep or p.info["pid"] == me:
                    continue
                total[n] = total.get(n, 0) + p.info["memory_info"].rss
            except Exception:
                continue
        heavy = [(n, b // (1024 * 1024)) for n, b in sorted(total.items(), key=lambda x: -x[1]) if b > 300 * 1024 * 1024][:4]
        mem = psutil.virtual_memory()
        names = [n[:-4] if n.endswith(".exe") else n for n, _ in heavy]
        if not heavy:
            temp = self._clean_temp()
            self._say_async(f"Оперативная память занята на {int(mem.percent)} процентов, тяжёлых программ нет. "
                            f"Очистил временные файлы: {temp} мегабайт.")
            return
        listing = ", ".join(names)
        self.emit("msg", who="sys", text="Больше всего памяти: " + ", ".join(f"{n} ({mb} МБ)" for n, mb in heavy))

        def do():
            closed = 0
            for n, _ in heavy:
                r = subprocess.run(["taskkill", "/IM", n], capture_output=True, creationflags=NO_WINDOW)   # мягко, без /F
                closed += r.returncode == 0
            freed = self._clean_temp()
            return f"Закрыл программ: {closed}. Временных файлов очищено: {freed} мегабайт."
        self.ask_yes_no(f"Память занята на {int(mem.percent)} процентов. Больше всего берут: {listing}. "
                        "Закрыть их и очистить временные файлы?", do)

    @staticmethod
    def _clean_temp():
        import tempfile
        root, freed, limit = tempfile.gettempdir(), 0, time.time() - 86400
        for dirpath, _, files in os.walk(root):
            for f in files:
                fp = os.path.join(dirpath, f)
                try:
                    if os.path.getmtime(fp) < limit:
                        size = os.path.getsize(fp)
                        os.remove(fp)
                        freed += size
                except Exception:
                    pass
        return freed // (1024 * 1024)

    # ----- память о пользователе -----
    def c_remember(self, args, j=None):
        m = re.search(r"запомни[\s,:\-—]*(?:что\s+)?(.*)", self.last_raw, re.I | re.S)
        fact = (m.group(1) if m and m.group(1).strip() else args).strip()
        if not fact:
            return "Что запомнить? Например: запомни, что я живу в Страсбурге."
        if len(self.profile.facts) >= 60:
            return "Моя память о вас переполнена. Скажите «забудь» и о чём, чтобы освободить место."
        self.profile.add(fact)
        return ph("ack") if random.random() < 0.0 else "Запомнил."

    def c_what_know(self, args, j=None):
        if not self.profile.facts:
            return "Пока ничего. Расскажите о себе, а потом скажите: запомни, и что именно."
        return "Вы мне рассказали: " + "; ".join(f.rstrip(".") for f in self.profile.facts[-12:]) + "."

    def c_forget(self, args, j=None):
        a = args.strip()
        if not a:
            return "Что забыть? Скажите, например: забудь про город, или: забудь всё о себе."
        if re.fullmatch(r"(все|всё|все обо мне|все что я говорил|все что я рассказал|все что запомнил|всю память)", a):
            n = self.profile.clear()
            return "Всё стёр." if n else "Мне и так нечего забывать."
        n = self.profile.forget(a)
        return "Забыл." if n else "Не нашёл такого среди запомненного."

    # ----- презентации и таблицы -----
    def c_make_file(self, kind, args):
        if self.writing:
            return "Я ещё работаю над предыдущим файлом. Скажите: отмени написание, чтобы остановить."
        text = args
        count = 6
        if kind == "pptx":
            count, text = writer.parse_count(text, 6)
        topic = writer.clean_topic(text)
        topic = writer.clean_topic(topic)
        if len(topic) < 3:
            return ("Какая тема? Например: сделай презентацию про космос." if kind == "pptx"
                    else "Какая таблица? Например: сделай таблицу расходов на месяц.")
        self.writing, self.write_cancel = True, False
        threading.Thread(target=self._make_job, args=(kind, topic, count), daemon=True).start()
        what = "презентацию" if kind == "pptx" else "таблицу"
        return f"Принял. Делаю {what}: {topic}. Это займёт несколько минут, сообщу, когда будет готово."

    def _make_job(self, kind, topic, count):
        try:
            if not self.llm_ready.wait(timeout=900) or self.llm_error:
                self.cmd_q.put(("say", self.llm_error or "Нейросеть не готова."))
                return
            model = self.cfg.get("writer_model") or self.cfg["ollama_model"]

            def ask(system, user, temp, num_predict, timeout):
                return self.ollama.chat(
                    [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    model=model, temperature=temp, num_predict=num_predict, num_ctx=4096, timeout=timeout)

            progress = lambda t: self.emit("msg", who="sys", text=t)
            try:
                if kind == "pptx":
                    slides = writer.generate_slides(ask, topic, count, progress, lambda: self.write_cancel)
                    if slides is None:
                        self.cmd_q.put(("say", "Хорошо, остановился."))
                        return
                    path = writer.save_pptx(topic, slides, DOCS_DIR)
                else:
                    headers, rows = writer.generate_table(ask, topic, progress)
                    path = writer.save_xlsx(topic, headers, rows, DOCS_DIR)
            except ImportError:
                lib = "python-pptx" if kind == "pptx" else "openpyxl"
                self.emit("msg", who="err", text=f"Не хватает библиотеки {lib}. Запустите INSTALL_JARVIS.bat ещё раз.")
                self.cmd_q.put(("say", "Не хватает библиотеки. Запустите INSTALL_JARVIS.bat ещё раз."))
                return
            self.emit("msg", who="sys", text="Файл сохранён: " + path)
            try:
                os.startfile(path)
            except Exception:
                pass
            self.cmd_q.put(("say", ph("write_done")))
        except Exception as e:
            traceback.print_exc()
            self.emit("msg", who="err", text="Не получилось сделать файл: " + str(e))
            self.cmd_q.put(("say", "Не получилось сделать файл. Подробности в файле logs/jarvis.log."))
        finally:
            self.writing = False

    # ----- музыка -----
    MUSIC_PLAYERS = {"spotify.exe", "yandexmusic.exe", "яндекс музыка.exe", "vlc.exe", "aimp.exe",
                     "foobar2000.exe", "itunes.exe", "wmplayer.exe", "musicbee.exe"}

    def _music_running(self):
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower() in self.MUSIC_PLAYERS:
                return True
        return False

    @staticmethod
    def _youtube_first(query):
        """Находит первое видео по запросу, чтобы открыть его сразу на воспроизведение."""
        import requests
        r = requests.get("https://www.youtube.com/results?search_query=" + urllib.parse.quote(query),
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                                  "Accept-Language": "ru-RU,ru;q=0.9"},
                         cookies={"CONSENT": "YES+1", "SOCS": "CAI"}, timeout=10)
        ids = re.findall(r'"videoId":"([\w-]{11})"', r.text)
        return ids[0] if ids else None

    def _play_web(self, query):
        try:
            vid = self._youtube_first(query)
        except Exception:
            traceback.print_exc()
            vid = None
        if vid:
            webbrowser.open(f"https://www.youtube.com/watch?v={vid}&autoplay=1")
        else:
            webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote(query))

    SOURCES = {"яндекс": "yandex", "вк": "vk", "вконтакте": "vk", "спотиф": "spotify", "ютуб": "youtube",
               "youtube": "youtube"}
    SOURCE_NAMES = {"yandex": "Яндекс Музыке", "vk": "VK Музыке", "spotify": "Spotify"}

    def _split_source(self, q):
        """«imagine dragons в яндекс музыке» -> ('yandex', 'imagine dragons')."""
        src = (self.cfg.get("music_source") or "youtube").lower()
        m = re.search(r"(?<!\w)(?:в|во|на|через)\s+(яндекс музык\w*|яндексе|вк музык\w*|вконтакте|вк|спотифай\w*|"
                      r"ютуб\w*|youtube)(?!\w)", q)
        if m:
            word = m.group(1)
            for k, v in self.SOURCES.items():
                if word.startswith(k):
                    src = v
                    break
            q = (q[:m.start()] + " " + q[m.end():])
        return src, " ".join(q.split())

    def _play_source(self, src, q):
        enc = urllib.parse.quote(q)
        if src == "yandex":
            webbrowser.open("https://music.yandex.ru/search?text=" + enc)
        elif src == "vk":
            webbrowser.open("https://vk.com/audio?q=" + enc)
        elif src == "spotify":
            try:
                os.startfile("spotify:search:" + enc)
            except OSError:
                webbrowser.open("https://open.spotify.com/search/" + enc)
        else:
            self._play_web(q)

    def _start_player_and_play(self, src="youtube"):
        """Запускает музыкальную программу и жмёт «воспроизвести», когда она загрузится."""
        names = [(self.cfg.get("music_app") or "").strip()]
        if src == "yandex":
            names.append("яндекс музыка")
        names.append(os.path.join(os.environ.get("APPDATA", ""), "Spotify", "Spotify.exe"))
        names.append("яндекс музыка")
        target = None
        for n in names:
            if not n:
                continue
            if os.path.exists(n):
                target = n
            else:
                e = self.index.find(n)
                target = e["path"] if e else None
            if target:
                break
        if not target:
            return False
        os.startfile(target)

        def later():
            time.sleep(9)
            tap_key(VK_MEDIA_PLAY)
        threading.Thread(target=later, daemon=True).start()
        return True

    def c_music(self, args, j=None):
        q = self._clean(args)
        src, q = self._split_source(q)
        q = re.sub(r"^(песню|трек|композицию|музыку|группы|группу|исполнителя|артиста)\s+", "", q).strip()
        if q:
            query = q + " музыка" if src == "youtube" and len(q.split()) < 3 else q
            threading.Thread(target=self._play_source, args=(src, query), daemon=True).start()
            if src == "youtube":
                return f"Включаю: {q}."
            return f"Открыл поиск в {self.SOURCE_NAMES[src]}: {q}. Нажмите на нужный трек, сам сервис запускать его без вас не даёт."
        if self._music_running():
            tap_key(VK_MEDIA_PLAY)
            return ph("ack")
        if self._start_player_and_play(src):
            return "Запускаю плеер и включаю музыку."
        urls = {"yandex": "https://music.yandex.ru", "vk": "https://vk.com/audio", "spotify": "https://open.spotify.com"}
        if src in urls:
            webbrowser.open(urls[src])
            return "Открыл музыкальный сервис. Выберите, что включить."
        threading.Thread(target=self._play_web, args=(self.cfg.get("music_default_query") or "музыка",),
                         daemon=True).start()
        return "Включаю музыку."

    def best_match(self, query, names, cutoff=0.6):
        """Помощник для плагинов: найти самое похожее название из списка."""
        q = norm(query)
        qs = {q, translit(q)}
        best, best_s = None, 0.0
        for n in names:
            nl = n.lower()
            for qq in qs:
                s = similarity(qq, nl)
                if qq and len(nl) >= 3 and (qq in nl or nl in qq):
                    s = max(s, 0.9)
                if s > best_s:
                    best, best_s = n, s
        return best if best_s >= cutoff else None

    # ----- разбор команд -----
    FAIL_PREFIXES = ("Не ", "В этой команде", "Ошибка", "Нет ")
    CANCEL_WORDS = ("отмена", "отмени", "не надо", "неважно", "ничего", "забудь", "стоп", "нет")

    def ph(self, key, **kw):
        return ph(key, **kw)

    def _fix_asr(self, raw):
        """Правка частых ошибок распознавания названий (Chrome, YouTube, Terraria и др.).
        Работает и для Whisper, и для запасного распознавания. Словарь — config/asr_corrections.json."""
        if not self.cfg.get("asr_fix_aliases", True):
            return raw
        if not getattr(self, "_asr_fixes", None):
            try:
                self._asr_fixes = asr_fix.load_fixes(BASE_DIR)
            except Exception:
                log.exception("словарь правок распознавания")
                self._asr_fixes = {}
        return asr_fix.apply_fixes(raw, self._asr_fixes)

    def handle(self, raw):
        self.last_raw = raw
        log.info("распознано: %r", raw)
        raw = self._fix_asr(raw)
        if raw != self.last_raw:
            log.info("после правки распознавания: %r", raw)
        text = norm(raw)
        if not text:
            self.say(ph("not_understood"))
            return
        if self.store.changed_on_disk():
            self.reload_custom()
        # 1. ответ на вопрос Джарвиса
        if self.pending:
            cb, expires, *rest = self.pending
            self.pending = None
            if time.time() < expires:
                if not (rest and rest[0]) and text in self.CANCEL_WORDS:
                    self.say("Хорошо, отменил.")
                    return
                try:
                    reply = cb(raw)
                except Exception:
                    log.exception("обработчик ответа")
                    reply = "Не получилось. Подробности в файле logs/assistant.log."
                if reply:
                    self.say(reply)
                return
        # 2. управление идущим сценарием: стоп / дальше / пропусти / отмена
        if self.scenarios.run is not None:
            r = self.scenarios.control(text)
            if r is not None:
                if r:
                    self.say(r)
                return
        text = nlu.strip_fillers(text, self.wake_words) or text
        # 3. «ещё», «подробнее», «про космос»: продолжение разговора с контентом
        again = {"еще", "еще одну", "давай еще", "давай еще одну", "другую", "следующую", "еще раз"}
        if text in again and self.content.last_kind:
            self.say(self.content.again())
            return
        if text in ("подробнее", "расскажи подробнее", "расскажи подробней", "а подробнее"):
            r = self.content.more()
            if r:
                self.say(r)
            return
        if text in ("факт", "шутка", "анекдот", "загадка"):
            self.say({"факт": self.content.fact, "шутка": self.content.joke, "анекдот": self.content.joke}
                     .get(text, lambda *a: self.content.riddle())(""))
            return
        if text.startswith("про ") and self.content.last_kind in ("fact", "joke"):
            tbl = self.content.facts if self.content.last_kind == "fact" else self.content.jokes
            if self.content.match_cat(text, tbl):
                self.say((self.content.fact if self.content.last_kind == "fact" else self.content.joke)(text))
                return
        # 4. цепочка из нескольких команд («открой ютуб, включи видео, перемотай…»)
        try:
            if self.scenarios.try_handle_text(raw):
                return
        except Exception:
            log.exception("разбор цепочки команд")
        # 5. одиночное действие из реестра (файлы, видео в браузере)
        try:
            hit = self.registry.match_clause(raw.strip())
            if hit and hit[0].name.split(".")[0] in ("file", "browser"):
                act, params = hit
                log.info("действие из реестра: %s %s", act.name, params)
                self.scenarios.start("", [self.scenarios._make_step(act.name, params, None)], {})
                return
        except Exception:
            log.exception("одиночное действие")
        # 6. обычные команды: точное совпадение -> синонимы -> леммы -> нечёткое
        m = nlu.match_command(text, self.custom + self.commands + self.builtin,
                              int(self.cfg.get("fuzzy_threshold", 80)))
        if m:
            log.info("команда: триггер=%r способ=%s уверенность=%.0f аргументы=%r источник=%s",
                     m.trig, m.how, m.score, m.args, getattr(m.cmd, "source", "?"))
            if m.how == "fuzzy" and m.score < 97:
                self.ask_yes_no(f"Ты имел в виду «{m.trig}»?", lambda m=m: self._run_match(m))
                return
            self._run_match(m)
            return
        self.answer(raw)

    def _run_match(self, m):
        try:
            reply = m.cmd.run(m.args, self)
        except Exception:
            log.exception("команда %r", m.trig)
            traceback.print_exc()
            reply = "В этой команде произошла ошибка. Подробности в файле logs/assistant.log."
        self.responder.note(not (isinstance(reply, str) and reply.startswith(self.FAIL_PREFIXES)))
        if reply:
            self.say(reply)
        return ""

    # ----- мосты для реестра, сценариев и мастера -----
    def site_url(self, name):
        sites = {norm(k): v for k, v in {**SITES, **self.cfg["sites"]}.items()}
        return sites.get(name)

    def resolve_app(self, name):
        n = norm(name)
        if not n:
            return None
        if os.path.exists(name.strip('"')):
            return name
        if any(norm(k) in n for k in self.cfg["apps"]):
            return n
        if self.site_url(n):
            return n
        b = self._match_builtin_app(n)
        if b:
            return b[0]
        q = self._resolve_alias(n)
        if re.match(r"^https?://", q):
            return q
        e = self.index.find(q)
        return e["name"] if e else None

    def suggest_apps(self, name, n=3, cutoff=0.55):
        q = translit(norm(self._resolve_alias(norm(name))))
        names = [e["name"] for e in self.index.entries]
        scored = sorted(((similarity(q, translit(norm(x))), x) for x in names), reverse=True)
        return [x for sc, x in scored[:n] if sc >= cutoff]

    def legacy_match(self, clause):
        return nlu.exact_match(norm(clause), self.custom + self.commands + self.builtin)

    def run_legacy(self, clause):
        m = self.legacy_match(clause)
        if not m:
            return ""
        try:
            return m.cmd.run(m.args, self) or ""
        except Exception:
            log.exception("legacy-шаг")
            return "Не получилось."

    def find_conflict(self, phrase):
        p = norm(phrase)
        for cmd in self.commands + self.builtin:
            for t in cmd.triggers:
                if t == p or p.startswith(t + " "):
                    return f"это встроенная команда «{t}»"
        it = self.store.find(phrase)
        if it:
            return "у тебя уже есть такая команда"
        return ""

    def reload_custom(self):
        self.store.load()
        self.custom = [Command(it["phrases"], lambda a, jj=None, it=it: self.scenarios.run_item(it, a), "моя команда")
                       for it in self.store.items]
        for name, sc in (self.cfg.get("scenes") or {}).items():
            phrases = sc.get("phrases") or [name]
            self.custom.append(Command(phrases, lambda a, jj=None, sc=sc: self.c_scene(sc), "сценарий"))
        for sc in (_load_cfg_json("scenarios.json", {}) or {}).get("scenarios", []):
            self.custom.append(Command(sc["phrases"], lambda a, jj=None, sc=sc: self.scenarios.run_item(sc, a), "сценарий"))
        self.emit("custom", n=len(self.store.items))

    def c_reload_cmds(self, args, j=None):
        self.reload_custom()
        return "Команды перезагрузил."

    def c_export_cmds(self, args, j=None):
        path = os.path.join(self.safety.folders["workdir"], "jarvis_commands.json")
        n = self.store.export_to(path)
        return f"Экспортировал {n} команд в файл jarvis_commands.json в рабочей папке."

    def c_import_cmds(self, args, j=None):
        path = os.path.join(self.safety.folders["workdir"], "jarvis_commands.json")
        if not os.path.exists(path):
            return "Положи файл jarvis_commands.json в папку Documents\\Jarvis и скажи ещё раз."
        added, skipped = self.store.import_from(path, self.registry)
        self.reload_custom()
        return f"Импортировал {added}, пропустил {skipped}."

    def c_save_adhoc(self, args, j=None):
        steps = self.scenarios.last_adhoc
        if not steps:
            return "Нет последней цепочки, которую можно сохранить."
        name = norm(args)
        if not name:
            self.ask("Как назвать эту команду?", lambda raw: self._save_adhoc(steps, raw), 40)
            return ""
        return self._save_adhoc(steps, name)

    def _save_adhoc(self, steps, name):
        clean = [{k: v for k, v in s.items() if k != "clause"} for s in steps]
        self.store.add([norm(name)], clean, "")
        self.reload_custom()
        return f"Сохранил. Теперь скажи: Джарвис, {norm(name)}."

    # ----- встроенные команды -----
    def c_stop(self, args, j=None):
        self.stop_event.set()
        return ph("bye")

    def c_time(self, args, j=None):
        n = datetime.datetime.now()
        hw = plural(n.hour, ("час", "часа", "часов"))
        if n.minute == 0:
            return ph("time0", h=n.hour, hw=hw)
        return ph("time", h=n.hour, hw=hw, m=n.minute, mw=plural(n.minute, ("минута", "минуты", "минут")))

    def c_date(self, args, j=None):
        n = datetime.datetime.now()
        return ph("date", wd=WEEKDAYS[n.weekday()], d=n.day, mo=MONTHS[n.month - 1], y=n.year)

    def c_site_search(self, url, label, args):
        q = args.strip()
        if not q:
            return "Что искать?"
        webbrowser.open(url.format(q=urllib.parse.quote(q)))
        return f"Ищу {q} {label}."

    def c_search(self, args, j=None):
        if not args:
            return "Что найти?"
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(args))
        return ph("search", x=args)

    def c_help(self, args, j=None):
        return ("Я открываю и закрываю программы, меняю громкость, управляю музыкой, делаю скриншоты, "
                "ставлю таймер, называю курсы валют и погоду, ищу в интернете, отвечаю на любые вопросы "
                "и пишу тексты: рефераты, эссе, письма, стихи. Делаю презентации и таблицы Excel. "
                "Запоминаю, что вы мне расскажете о себе: скажите «запомни». "
                "Включаю музыку: скажите «включи песню» и название, можно добавить «в яндекс музыке». "
                "Открываю Яндекс, Кинопоиск, Авито, Озон, Госуслуги и другие российские сервисы, "
                "ищу на них: скажите, например, «найди на авито велосипед». "
                "Новые команды добавляются на вкладке Команды.")

    def c_reload(self, args, j=None):
        info = self.load_plugins()
        return f"Плагинов загружено: {sum(1 for p in info if p['ok'])}."

    @staticmethod
    def _clean(args):
        return " ".join(w for w in args.split() if w not in FILLER)

    def _match_builtin_app(self, name):
        for names, launch, procs in APPS_BUILTIN:
            if any(name.startswith(n) or n in name for n in names):
                return names[0], launch, procs
        return None

    def _resolve_alias(self, name):
        for k in sorted(ALIASES, key=len, reverse=True):
            if name == k or name.startswith(k + " "):
                return ALIASES[k]
        return name

    def c_open(self, args, j=None):
        name = self._clean(args)
        if not name:
            return "Что открыть?"
        for key, appname in DUAL_APPS.items():
            if name == key or name.startswith(key + " "):
                e = self.index.find(appname)
                if e:
                    os.startfile(e["path"])
                    return ph("open", x=e["name"])
        sites = {norm(k): v for k, v in {**SITES, **self.cfg["sites"]}.items()}
        for key, url in sorted(sites.items(), key=lambda kv: -len(kv[0])):
            if name == key or name.startswith(key + " "):
                webbrowser.open(url)
                return ph("open", x=key)
        for key, path in self.cfg["apps"].items():
            if norm(key) in name:
                os.startfile(path)
                return ph("open", x=key)
        b = self._match_builtin_app(name)
        if b:
            os.startfile(b[1])
            return ph("open", x=b[0])
        q = self._resolve_alias(name)
        if re.match(r"^https?://", q):
            webbrowser.open(q)
            return ph("open", x=name)
        e = self.index.find(q)
        if e:
            os.startfile(e["path"])
            return ph("open", x=e["name"])
        try:
            os.startfile(q)
            return "Пробую открыть " + name
        except OSError:
            sugg = self.suggest_apps(name, 1, 0.6)
            if sugg:                                              # тихий провал не нужен: переспрашиваем
                target = self.index.find(norm(sugg[0]))
                if target:
                    self.ask_yes_no(f"Ты имел в виду {sugg[0]}?",
                                    lambda t=target: (os.startfile(t["path"]), ph("open", x=t["name"]))[1])
                    return ""
            return "Не нашёл программу " + name

    def c_close(self, args, j=None):
        name = self._clean(args)
        if not name:
            return "Что закрыть?"
        exes, label = set(), name
        b = self._match_builtin_app(name)
        if b:
            label = b[0]
            exes |= set(b[2])
        for key, path in self.cfg["apps"].items():
            if norm(key) in name:
                exes.add(os.path.basename(path).lower())
        q = self._resolve_alias(name)
        e = self.index.find(q)
        if e and e["exe"] and e["exe"] not in GENERIC_EXES:
            exes.add(e["exe"])
        qt = translit(q)
        running = set()
        for p in psutil.process_iter(["name"]):
            pn = (p.info.get("name") or "").lower()
            if not pn or pn in PROTECTED:
                continue
            base = pn[:-4] if pn.endswith(".exe") else pn
            if (pn in exes
                    or (len(qt) >= 4 and (qt in base or (len(base) >= 4 and base in qt)))
                    or (len(qt) >= 4 and similarity(qt, base) >= 0.8)):
                running.add(pn)
        if not running:
            return "Не вижу запущенной программы " + name
        for pn in running:
            r = subprocess.run(["taskkill", "/IM", pn], capture_output=True, creationflags=NO_WINDOW)
            if r.returncode != 0:
                subprocess.run(["taskkill", "/F", "/IM", pn], capture_output=True, creationflags=NO_WINDOW)
        return ph("close", x=label)

    def c_vol_up(self, args, j=None):
        for _ in range(5):
            tap_key(VK_VOLUME_UP)
        return ph("louder")

    def c_vol_down(self, args, j=None):
        for _ in range(5):
            tap_key(VK_VOLUME_DOWN)
        return ph("quieter")

    def c_mute(self, args, j=None):
        tap_key(VK_VOLUME_MUTE)
        return ph("ack")

    def c_play(self, args, j=None):
        tap_key(VK_MEDIA_PLAY)
        return ph("ack")

    def c_next(self, args, j=None):
        tap_key(VK_MEDIA_NEXT)
        return "Следующий трек."

    def c_prev(self, args, j=None):
        tap_key(VK_MEDIA_PREV)
        return "Предыдущий трек."

    def c_desktop(self, args, j=None):
        tap_key(VK_D, VK_LWIN)
        return ph("ack")

    def c_lock(self, args, j=None):
        return self._danger_via_registry_lock()

    def _danger_via_registry_lock(self):
        self.ask_yes_no("Заблокировать компьютер?", lambda: (ctypes.windll.user32.LockWorkStation(), "Заблокировал.")[1])
        return ""

    def c_screenshot(self, args, j=None):
        try:
            from PIL import ImageGrab
        except ImportError:
            return "Для скриншотов нужна библиотека pillow."
        folder = os.path.join(os.path.expanduser("~"), "Pictures", "Jarvis")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, datetime.datetime.now().strftime("screen_%Y%m%d_%H%M%S.png"))
        ImageGrab.grab().save(path)
        return ph("shot")

    def _danger_via_registry(self, name, question):
        approved = Ctx(self)
        approved.confirm = lambda text: True
        self.ask_yes_no(question, lambda: self.registry.execute(name, {}, approved).msg)
        return ""

    def c_shutdown(self, args, j=None):
        return self._danger_via_registry("system.shutdown", "Выключить компьютер?")

    def c_restart(self, args, j=None):
        return self._danger_via_registry("system.restart", "Перезагрузить компьютер?")

    def c_cancel_shutdown(self, args, j=None):
        subprocess.run(["shutdown", "/a"], capture_output=True, creationflags=NO_WINDOW)
        return "Отменил."

    def c_sysinfo(self, args, j=None):
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory().percent
        s = f"Процессор загружен на {cpu:.0f} процентов, память на {ram:.0f}."
        b = psutil.sensors_battery()
        if b:
            s += f" Заряд батареи {b.percent:.0f} процентов" + (", идёт зарядка." if b.power_plugged else ".")
        return s

    def c_timer(self, args, j=None):
        sec = parse_duration(args)
        if not sec:
            return "На сколько поставить таймер? Например: таймер на пять минут."
        t = threading.Timer(sec, lambda: self.cmd_q.put(("say", "Таймер сработал.")))
        t.daemon = True
        t.start()
        return ph("timer", x=fmt_duration(sec))

    # ----- свои команды (вкладка «Команды») -----
    def load_custom(self):
        self.reload_custom()

    def run_target(self, target):
        """Открывает сайт, файл или программу. Возвращает текст ошибки или None."""
        t = (target or "").strip().strip('"')
        try:
            if re.match(r"^(https?://|www\.)", t, re.I):
                webbrowser.open(t if t.lower().startswith("http") else "https://" + t)
            elif re.match(r"^[\w-]+(\.[\w-]+)*\.(com|ru|org|net|io|tv|me|by|ua|su|рф)(/\S*)?$", t, re.I) \
                    and not os.path.exists(t):
                webbrowser.open("https://" + t)
            elif os.path.exists(t):
                os.startfile(t)
            elif re.match(r"^[\w+.-]{2,}:", t):      # ms-settings:, spotify: и т. п.
                os.startfile(t)
            elif re.match(r"^[A-Za-z]:|^\\\\|[\\/]", t):
                return "Не нашёл файл " + os.path.basename(t)
            elif t.lower().endswith((".exe", ".lnk", ".bat", ".cmd")):
                os.startfile(t)                       # например notepad.exe: Windows найдёт сам
            else:                                     # просто название программы из меню Пуск
                msg = self.c_open(norm(t))
                if msg.startswith("Не нашёл"):
                    return msg
        except Exception as e:
            traceback.print_exc()
            return "Не получилось открыть: " + str(e)
        return None

    def custom_view(self):
        """Список команд для вкладки «Команды»: фраза, краткое описание шагов, ответ."""
        out = []
        for it in self.store.items:
            parts = []
            for st in it["steps"]:
                act = self.registry.get(st["action"])
                parts.append(act.describe(st.get("params", {})) if act else st["action"])
            out.append({"id": it["id"], "phrase": it["phrases"][0], "target": " → ".join(parts),
                        "reply": it.get("reply", ""), "steps": len(it["steps"])})
        return out

    def add_custom(self, phrase, target, reply=""):
        phrase = norm(phrase or "")
        for w in self.wake_words:
            phrase = re.sub(r"(?<!\w)" + re.escape(w) + r"(?!\w)", " ", phrase)
        phrase = " ".join(phrase.split())
        target = (target or "").strip()
        if not phrase:
            return {"ok": False, "error": "Введите фразу, по которой команда будет срабатывать."}
        if not target:
            return {"ok": False, "error": "Укажите сайт или программу, которую нужно открыть."}
        conflict = self.find_conflict(phrase)
        if conflict and not self.store.find(phrase):
            return {"ok": False, "error": f"Фраза занята: {conflict}. Выберите другую или создайте команду через мастер."}
        self.store.add([phrase], [_legacy_step(target)], (reply or "").strip())
        self.reload_custom()
        return {"ok": True, "list": self.custom_view()}

    def delete_custom(self, index):
        items = self.store.items
        if 0 <= index < len(items):
            self.store.delete(items[index]["id"])
            self.reload_custom()
        return self.custom_view()

    def test_custom(self, index):
        if 0 <= index < len(self.store.items):
            self.scenarios.run_item(self.store.items[index])
            return ""
        return "Команда не найдена."

    # ----- голос (вкладка «Голос») -----
    def load_settings(self):
        self.settings = {"voice_id": "", "rate": self.cfg["voice_rate"], "volume": 100}
        try:
            with open(SETTINGS_PATH, encoding="utf-8-sig") as f:
                self.settings.update(json.load(f))
        except FileNotFoundError:
            pass
        except Exception:
            traceback.print_exc()

    def save_settings(self):
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(self.settings, f, ensure_ascii=False, indent=2)

    def get_voice(self):
        vs = sorted(self.speaker.voices if self.speaker else [], key=lambda v: not v["ru"])
        return {"engine": getattr(self.speaker, "engine", "sapi"), "voices": vs, "current": self.settings.get("voice_id", ""),
                "rate": int(self.settings["rate"]), "volume": int(self.settings["volume"])}

    def set_voice(self, voice_id, rate, volume):
        self.settings.update({"voice_id": voice_id or "",
                              "rate": max(80, min(320, int(rate))),
                              "volume": max(0, min(100, int(volume)))})
        self.save_settings()
        if self.speaker is not None:
            self.speaker.apply()

    def test_voice(self):
        text = "Здравствуйте. Я Джарвис. Так звучит мой голос."
        if self.running:
            self.cmd_q.put(("say", text))
        elif self.speaker is not None:
            self.speaker.speak(text, wait=False)

    # ----- главный рабочий поток -----
    def _worker(self):
        try:
            self.set_state("loading")
            try:
                import comtypes
                comtypes.CoInitialize()
            except Exception:
                pass
            mode = str(self.cfg.get("stt_engine", "auto")).lower()
            self.stt_ready.wait(timeout=120)
            if self.model is None and self.whisper is None and mode != "vosk" and stt.whisper_installed() \
                    and not self.whisper_failed:
                # Не ждём Whisper: начинаем слушать сразу (на Vosk), а к Whisper переключимся,
                # как только он догрузится - это и даёт быстрый старт.
                wait = float(self.cfg.get("whisper_start_wait", 6))
                self.emit("msg", who="sys", text="Готовлю точное распознавание, начинаю слушать…")
                end = time.time() + max(0.0, wait)
                while self.whisper is None and not self.whisper_failed and time.time() < end:
                    time.sleep(0.2)
            if self.model is None and self.whisper is None:
                if not os.path.isdir(MODEL_PATH):
                    # Запасной модели нет: без Whisper слушать нечем. Честно ждём его, а не падаем.
                    if stt.whisper_installed() and not self.whisper_failed:
                        self.emit("msg", who="sys", text="Жду загрузку точного распознавания речи…")
                        end = time.time() + 180
                        while self.whisper is None and not self.whisper_failed and time.time() < end:
                            time.sleep(0.3)
                    if self.whisper is None:
                        self.emit("msg", who="err",
                                  text="Не найдена модель распознавания речи. Запустите INSTALL_JARVIS.bat "
                                       "или Install_Whisper.bat.")
                        return
                else:
                    self.emit("msg", who="sys", text="Загружаю модель распознавания речи…")
                    self.model = vosk_api().Model(MODEL_PATH)
            if self.speaker is None or not self.speaker.ready.wait(timeout=8) or not self.speaker.ok:
                self.emit("msg", who="err",
                          text="Голос Windows не отвечает, работаю без озвучки. "
                               "Выберите другой голос на вкладке «Голос» или посмотрите файл logs/jarvis.log.")

            def make_rec():
                if self.whisper is not None and self.whisper.ok and mode != "vosk":
                    return stt.WhisperRecognizer(self.whisper, int(self.cfg.get("mic_sensitivity", 3))), "whisper"
                return stt.VoskRecognizer(vosk_api().KaldiRecognizer(self.model, SAMPLE_RATE),
                                          bool(self.cfg.get("mic_boost", True))), "vosk"

            rec, kind = make_rec()
            self.stage("слушаю (" + kind + ")")
            audio_q = queue.Queue()

            def callback(indata, frames, time_info, status):
                raw = bytes(indata)
                audio_q.put(raw)
                try:
                    samples = array.array("h", raw)[::16]
                    rms = (sum(s * s for s in samples) / max(1, len(samples))) ** 0.5
                    self.level = min(1.0, rms / 2500.0)
                except Exception:
                    pass

            def reset_audio():
                while not audio_q.empty():
                    audio_q.get_nowait()
                rec.reset()

            self.say(ph("start"))
            with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=1600, dtype="int16",
                                   channels=1, callback=callback):
                reset_audio()
                self.set_state("listening")
                while not self.stop_event.is_set():
                    # 1) очередь: набранные команды и голосовые уведомления (таймер)
                    try:
                        kind, payload = self.cmd_q.get_nowait()
                        if kind == "text":
                            self.emit("msg", who="you", text=payload)
                            self.handle(payload)
                        elif kind == "say":
                            self.say(payload)
                        elif kind == "read":
                            self.say(payload, max_sent=40, max_chars=2500)
                        reset_audio()
                        continue
                    except queue.Empty:
                        pass
                    # 2) микрофон
                    try:
                        data = audio_q.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    # Whisper догрузился (или сломался) - переключаемся на лету
                    if kind == "vosk" and mode != "vosk" and self.whisper is not None and self.whisper.ok:
                        rec, kind = make_rec()
                        self.emit("msg", who="sys", text="Распознавание речи улучшено: подключён Whisper.")
                        self.stage("переключился на Whisper")
                    elif kind == "whisper" and not self.whisper.ok and self.model is not None:
                        rec, kind = make_rec()
                        self.emit("msg", who="err", text="Whisper перестал отвечать, вернулся к обычному распознаванию.")
                    result = rec.accept(data)
                    if result is None:
                        continue
                    text = norm(result)
                    if not text:
                        continue
                    cmd, woke = "", False
                    for w in self.wake_words:
                        if w in text:
                            cmd, woke = text.split(w, 1)[1].strip(), True
                            break
                    if not woke and time.time() < self.awake_until:
                        cmd, woke = text, True       # продолжение диалога без слова «Джарвис»
                    if not woke:
                        continue
                    self.emit("msg", who="you", text=text)
                    self.handle(cmd)
                    self.awake_until = time.time() + (25 if self.pending else 6)
                    reset_audio()
        except Exception as e:
            traceback.print_exc()
            self.emit("msg", who="err", text=f"Ошибка: {e}")
        finally:
            self.running = False
            self.level = 0.0
            try:
                if self.speaker is not None:
                    self.speaker.stop()
            except Exception:
                pass
            self.set_state("off")


# ---------------- Мост между окном и ядром ----------------
class Api:
    """Эти методы доступны из JavaScript. Имена с «_» скрыты от окна."""

    def __init__(self, core):
        self._core = core

    def poll(self):
        return self._core.poll()

    def get_info(self):
        return self._core.info()

    def toggle(self):
        self._core.toggle()

    def send_text(self, text):
        self._core.send_text(text)

    def reload_plugins(self):
        self._core.load_plugins()

    def open_plugins_folder(self):
        os.startfile(PLUGINS_DIR)

    def get_custom(self):
        return self._core.custom_view()

    def add_custom(self, phrase, target, reply=""):
        return self._core.add_custom(phrase, target, reply)

    def delete_custom(self, index):
        return self._core.delete_custom(int(index))

    def test_custom(self, index):
        return self._core.test_custom(int(index))

    def start_wizard(self):
        if not self._core.running:
            self._core.emit("msg", who="sys", text="Сначала нажмите «Включить», мастер задаёт вопросы голосом и в чате.")
            return False
        self._core.cmd_q.put(("text", "добавь команду"))
        return True

    def export_commands(self):
        try:
            return {"ok": True, "text": self._core.c_export_cmds("")}
        except Exception as e:
            return {"ok": False, "text": str(e)}

    def import_commands(self):
        try:
            return {"ok": True, "text": self._core.c_import_cmds("")}
        except Exception as e:
            return {"ok": False, "text": str(e)}

    def pick_file(self):
        try:
            try:
                dlg = webview.FileDialog.OPEN
            except AttributeError:
                dlg = webview.OPEN_DIALOG
            res = webview.windows[0].create_file_dialog(
                dlg, file_types=("Программы и ярлыки (*.exe;*.lnk;*.bat;*.cmd)", "Все файлы (*.*)"))
            return res[0] if res else ""
        except Exception:
            traceback.print_exc()
            return ""

    def get_voice(self):
        return self._core.get_voice()

    def set_voice(self, voice_id, rate, volume):
        self._core.set_voice(voice_id, rate, volume)

    def test_voice(self):
        self._core.test_voice()

    def stop_speaking(self):
        self._core.hush()

    def minimize(self):
        if TRAY.get("icon") is not None:
            webview.windows[0].hide()            # в трей: окна на панели задач нет
        else:
            webview.windows[0].minimize()

    def close(self):
        self._core.shutdown()
        webview.windows[0].destroy()


TRAY = {"icon": None}


def start_tray(core):
    """Значок в области уведомлений: показать окно, включить/выключить слушание, выйти."""
    try:
        import pystray
    except ImportError:
        return

    def show(icon=None, item=None):
        try:
            webview.windows[0].show()
            webview.windows[0].restore()
        except Exception:
            pass

    def toggle(icon=None, item=None):
        core.toggle()

    def quit_app(icon=None, item=None):
        try:
            core.shutdown()
            icon.stop()
            webview.windows[0].destroy()
        except Exception:
            os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Показать Джарвиса", show, default=True),
        pystray.MenuItem("Слушать / остановить", toggle),
        pystray.MenuItem("Выход", quit_app))
    from core import icon as icon_mod
    icon = pystray.Icon("Jarvis", icon_mod.tray_image(64), "Джарвис", menu)
    TRAY["icon"] = icon
    icon.run_detached()


def main():
    core = Core()
    _startup.mark("ядро создано")
    api = Api(core)
    if _icon is not None:
        _icon.apply_when_ready("Джарвис")                 # иконка ставится сразу, как только окно появилось
    threading.Thread(target=core.bootstrap, daemon=True).start()
    window = webview.create_window(
        "Джарвис", UI_PATH, js_api=api, width=1140, height=740, min_size=(940, 620),
        frameless=FRAMELESS, easy_drag=False, background_color="#05070d")
    window.events.closed += lambda: core.shutdown()

    def on_start():
        _startup.mark("окно показано")
        start_tray(core)
        _startup.mark("трей готов")
        if core.cfg.get("autostart"):
            core.toggle()

    webview.start(on_start)


if __name__ == "__main__":
    if not _single_instance():
        try:
            ctypes.windll.user32.MessageBoxW(0, "Джарвис уже запущен. Ищите его значок в трее (возле часов).", "Джарвис", 0x40)
        except Exception:
            pass
        sys.exit(0)
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
