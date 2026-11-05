# -*- coding: utf-8 -*-
"""Самодиагностика Джарвиса: проверяет всё, что нужно для запуска, и пишет отчёт в diagnose.txt.
Запуск: CHECK_JARVIS.bat. Если что-то не работает, пришлите файл diagnose.txt."""
import importlib
import json
import os
import platform
import shutil
import struct
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
lines, bad = [], []


def out(text=""):
    print(text, flush=True)
    lines.append(text)


def ok(msg):
    out("  [OK]   " + msg)


def warn(msg):
    out("  [ВНИМ] " + msg)


def fail(msg, fix=""):
    out("  [ОШИБ] " + msg)
    if fix:
        out("         Что делать: " + fix)
    bad.append(msg)


def section(t):
    out("")
    out("== " + t + " ==")


def check_system():
    section("Система")
    out(f"  Windows: {platform.platform()}")
    out(f"  Python: {sys.version.split()[0]} ({struct.calcsize('P') * 8} бит) {sys.executable}")
    if os.name != "nt":
        warn("Это не Windows: часть проверок пропущена.")
    if struct.calcsize("P") * 8 != 64:
        fail("Python 32-битный", "запустите INSTALL_JARVIS.bat на 64-разрядной Windows")
    if "runtime" not in sys.executable.lower():
        warn("Запущено не встроенным Python из папки runtime. Для проверки используйте CHECK_JARVIS.bat.")
    if any(ord(c) > 127 for c in BASE):
        warn(f"В пути есть не-латинские символы: {BASE}. Piper может не работать. Лучше C:\\Jarvis.")
    else:
        ok("Путь к папке без русских букв")
    try:
        t = os.path.join(BASE, "_write_test.tmp")
        with open(t, "w") as f:
            f.write("x")
        os.remove(t)
        ok("Папка доступна для записи")
    except Exception as e:
        fail(f"Нельзя писать в папку Джарвиса: {e}", "перенесите папку из Program Files / архива в C:\\Jarvis")
    try:
        free = shutil.disk_usage(BASE).free / 1e9
        (ok if free >= 2 else fail)(f"Свободно места: {free:.1f} ГБ")
    except Exception:
        pass


def check_libs():
    section("Библиотеки Python")
    mods = [("vosk", "распознавание Vosk"), ("sounddevice", "микрофон и звук"), ("webview", "окно (pywebview)"),
            ("psutil", "процессы"), ("requests", "сеть"), ("comtypes", "громкость Windows"),
            ("pystray", "значок в трее"), ("PIL", "картинки"), ("docx", "Word"), ("pptx", "PowerPoint"),
            ("openpyxl", "Excel"), ("ddgs", "поиск в интернете"), ("pymorphy3", "падежи"),
            ("rapidfuzz", "нечёткий поиск"), ("websocket", "управление браузером")]
    if os.name == "nt":
        mods.append(("clr", "pythonnet для окна"))
    for m, what in mods:
        try:
            importlib.import_module(m)
            ok(f"{m} ({what})")
        except Exception as e:
            fail(f"{m} ({what}) не импортируется: {e}", "запустите INSTALL_JARVIS.bat ещё раз")


def check_files():
    section("Файлы Джарвиса")
    must = ["jarvis.py", "ui/index.html", "config.json", "core/nlu.py", "config/responses.json"]
    for rel in must:
        (ok if os.path.isfile(os.path.join(BASE, *rel.split("/"))) else fail)(
            rel if os.path.isfile(os.path.join(BASE, *rel.split("/"))) else f"нет файла {rel}")
    try:
        with open(os.path.join(BASE, "config.json"), encoding="utf-8-sig") as f:
            json.load(f)
        ok("config.json читается")
    except Exception as e:
        fail(f"config.json повреждён: {e}", "удалите его: Джарвис создаст настройки по умолчанию")
    model = os.path.join(BASE, "model")
    if os.path.isdir(model) and os.listdir(model):
        ok("Модель распознавания Vosk (папка model)")
    else:
        fail("Нет модели Vosk (папка model)", "запустите INSTALL_JARVIS.bat: он скачает модель")
    if os.path.isfile(os.path.join(BASE, "piper", "piper.exe")):
        ok("Piper (нейросетевой голос)")
    else:
        warn("Нет piper\\piper.exe: будет голос Windows. Для живого голоса запустите INSTALL_JARVIS.bat")
    try:
        v = [f for f in os.listdir(os.path.join(BASE, "voices")) if f.endswith(".onnx")]
        (ok if v else warn)(f"Голосов Piper: {len(v)}")
    except Exception:
        warn("Нет папки voices")


def check_webview2():
    section("Окно интерфейса (WebView2)")
    if os.name != "nt":
        return
    try:
        import winreg
        guid = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node" + guid[8:]),
                          (winreg.HKEY_LOCAL_MACHINE, guid), (winreg.HKEY_CURRENT_USER, guid)):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    ver, _ = winreg.QueryValueEx(k, "pv")
                ok(f"WebView2 установлен, версия {ver}")
                return
            except OSError:
                continue
        fail("WebView2 не найден", "установите «Evergreen WebView2 Runtime» с microsoft.com или запустите INSTALL_JARVIS.bat")
    except Exception as e:
        warn(f"Не удалось проверить WebView2: {e}")


def check_audio():
    section("Микрофон и динамики")
    try:
        import sounddevice as sd
        devs = sd.query_devices()
        ins = [d["name"] for d in devs if d["max_input_channels"] > 0]
        outs = [d["name"] for d in devs if d["max_output_channels"] > 0]
        (ok if ins else fail)(f"Микрофонов: {len(ins)}" + (f" (по умолчанию: {sd.query_devices(kind='input')['name']})" if ins else ""))
        if not ins:
            out("         Что делать: подключите микрофон; Параметры Windows -> Конфиденциальность -> Микрофон -> разрешить классическим приложениям")
        (ok if outs else fail)(f"Устройств вывода звука: {len(outs)}")
    except Exception as e:
        fail(f"Звук недоступен: {e}")


def check_ollama():
    section("Нейросеть (Ollama)")
    try:
        from llm import Ollama
        cfg = {}
        try:
            with open(os.path.join(BASE, "config.json"), encoding="utf-8-sig") as f:
                cfg = json.load(f)
        except Exception:
            pass
        cfg.setdefault("ollama_url", "http://localhost:11434/api/chat")
        cfg.setdefault("ollama_model", "qwen2.5:3b")
        o = Ollama(cfg)
        exe = o.find_exe()
        (ok if exe else fail)(f"ollama.exe: {exe}" if exe else "Ollama не установлена")
        if not exe:
            out("         Что делать: запустите INSTALL_JARVIS.bat или поставьте с ollama.com/download")
        if o.up():
            ok("Ollama запущена")
            (ok if o.have(cfg["ollama_model"]) else warn)(f"Модель {cfg['ollama_model']} " +
                                                          ("скачана" if o.have(cfg["ollama_model"]) else
                                                           "не скачана (Джарвис скачает сам при первом запуске)"))
        else:
            warn("Ollama сейчас не запущена (Джарвис запустит её сам)")
    except Exception as e:
        warn(f"Проверка Ollama не удалась: {e}")


def main():
    out("ДИАГНОСТИКА ДЖАРВИСА")
    for fn in (check_system, check_libs, check_files, check_webview2, check_audio, check_ollama):
        try:
            fn()
        except Exception as e:
            fail(f"Проверка {fn.__name__} упала: {e}")
    out("")
    out("ИТОГ: " + ("всё в порядке, Джарвис должен запускаться." if not bad else f"проблем: {len(bad)}. Исправьте пункты [ОШИБ] выше."))
    try:
        with open(os.path.join(BASE, "diagnose.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        out("Отчёт сохранён: diagnose.txt")
    except Exception:
        pass
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
