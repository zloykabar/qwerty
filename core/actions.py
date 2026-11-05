# -*- coding: utf-8 -*-
"""Каталог действий Джарвиса. Всё, что можно собрать в сценарий или пользовательскую команду, зарегистрировано здесь."""
import fnmatch
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import webbrowser

from core import nlu
from core.registry import Action, Param, Registry, Result
from core.safety import Safety, recycle, NO_WINDOW
from core.logsetup import get

log = get("actions")
WEB_CLOSE_TAB = {"ютуб", "ютубе", "youtube", "вкладку", "вкладка", "эту вкладку", "сайт", "страницу"}
WEB_CLOSE_BROWSER = {"браузер", "хром", "chrome", "эдж", "edge", "гугл хром"}
TEXT_EXT = (".txt", ".md", ".csv", ".log", ".json", ".py", ".html", ".xml", ".ini")


def _cap(s):
    s = (s or "").strip()
    return s[:1].upper() + s[1:]


def _strip_prep(s, preps=("про", "о", "об", "на тему", "с названием", "под названием", "называется", "название")):
    t = s.strip()
    for p in sorted(preps, key=len, reverse=True):
        if t.lower().startswith(p + " "):
            return t[len(p):].strip()
    return t


def split_where(rest):
    """«Отчёты на рабочем столе» -> («Отчёты», путь папки)."""
    m = re.search(r"\s+(?:на|в|во)\s+(рабочем столе|рабочий стол|документах|документы|загрузках|загрузки|изображениях"
                  r"|картинках)\s*$", rest.strip(), re.I)
    if m:
        return rest[:m.start()].strip(), m.group(1)
    return rest.strip(), ""


# ---------------------------------------------------------------- файлы
def find_files(core, name="", contains="", where="", limit=8, seconds=8.0):
    roots = [core.safety.place(where)] if where else [core.safety.folders[k] for k in
                                                       ("workdir", "desktop", "documents", "downloads")]
    seen, out = set(), []
    pat = name.lower().replace("ё", "е").strip()
    is_mask = any(c in pat for c in "*?")
    end = time.time() + seconds
    for root in roots:
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith(".") and x.lower() not in ("node_modules", "appdata")]
            if d.count(os.sep) - root.count(os.sep) > 6 or time.time() > end:
                break
            for f in files + dirs:
                low = f.lower().replace("ё", "е")
                if pat and not (fnmatch.fnmatch(low, pat) if is_mask else pat in low):
                    continue
                p = os.path.join(d, f)
                if p in seen:
                    continue
                if contains:
                    if not (os.path.isfile(p) and f.lower().endswith(TEXT_EXT) and os.path.getsize(p) < 2_000_000):
                        continue
                    try:
                        with open(p, encoding="utf-8", errors="ignore") as fh:
                            if contains.lower() not in fh.read().lower():
                                continue
                    except OSError:
                        continue
                seen.add(p)
                out.append(p)
                if len(out) >= limit:
                    return out
    return out


def _resolve_path(core, name, where=""):
    """Имя + место -> полный путь. Если дали полный путь, берём его как есть."""
    name = (name or "").strip().strip('"')
    if os.path.isabs(name):
        return name
    base = core.safety.place(where) if where else core.safety.folders["workdir"]
    return os.path.join(base, name)


def _check_path(core, ctx, path, verb):
    """Проверка прав: запрещено -> отказ, вне разрешённых каталогов -> подтверждение голосом."""
    cls = core.safety.classify(path)
    if cls == "forbidden":
        return Result(False, "это системная зона, туда я не лезу: " + os.path.basename(path))
    if cls == "confirm" and not ctx.confirm(f"{verb} вне рабочих папок: {path}."):
        return Result(False, "без твоего «да» не трогаю")
    return None


def h_find(core, p, ctx):
    hits = find_files(core, p.get("name", ""), p.get("contains", ""), p.get("where", ""))
    core.last_found = hits
    if not hits:
        return Result(False, "ничего не нашёл")
    names = ", ".join(os.path.basename(h) for h in hits[:3])
    more = f" и ещё {len(hits) - 3}" if len(hits) > 3 else ""
    return Result(True, f"Нашёл {len(hits)}: {names}{more}. Лежит в папке {os.path.dirname(hits[0])}.", hits)


def h_create_folder(core, p, ctx):
    path = _resolve_path(core, p["name"], p.get("where", ""))
    bad = _check_path(core, ctx, path, "Создаю папку")
    if bad:
        return bad
    if os.path.isdir(path):
        return Result(True, "Такая папка уже есть: " + os.path.basename(path))
    os.makedirs(path, exist_ok=True)
    return Result(True, f"Папку «{os.path.basename(path)}» создал.", path)


def h_create_file(core, p, ctx):
    path = _resolve_path(core, p["name"], p.get("where", ""))
    if not os.path.splitext(path)[1]:
        path += ".txt"
    bad = _check_path(core, ctx, path, "Создаю файл")
    if bad:
        return bad
    if os.path.exists(path) and not ctx.confirm(f"Файл {os.path.basename(path)} уже есть, перезаписать?"):
        return Result(False, "оставил как было")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(p.get("text", ""))
    return Result(True, f"Файл «{os.path.basename(path)}» создал.", path)


def _find_one(core, name, where="", want_dir=None):
    hits = find_files(core, name, where=where, limit=20)
    if want_dir is not None:
        hits = [h for h in hits if os.path.isdir(h) == want_dir]
    return hits[0] if hits else None


def h_open_folder(core, p, ctx):
    target = p.get("name", "")
    path = target if os.path.isdir(target) else _find_one(core, target, p.get("where", ""), True)
    if not path:
        return Result(False, "не нашёл такую папку")
    subprocess.Popen(["explorer", os.path.abspath(path)])
    return Result(True, f"Открыл папку {os.path.basename(path)}.")


def h_delete(core, p, ctx):
    name = (p.get("name") or "").strip()
    if nlu.norm(name) in ("все", "всё", "все файлы", "всю систему", "систему", "все папки"):
        wd = core.safety.folders["workdir"]
        items = os.listdir(wd) if os.path.isdir(wd) else []
        if not items:
            return Result(True, "В рабочей папке и так пусто.")
        if not ctx.confirm(f"Это переместит в Корзину всё содержимое рабочей папки {wd}, {len(items)} элементов."):
            return Result(False, "ничего не удалил")
        bad = 0
        for it in items:
            if not recycle(os.path.join(wd, it)):
                bad += 1
        return Result(bad == 0, "Рабочая папка очищена, всё лежит в Корзине." if not bad else f"Не удалось убрать {bad} элементов.")
    path = name if os.path.exists(name) else (core.last_found[0] if name in ("его", "это", "найденный", "найденное")
                                              and getattr(core, "last_found", None) else _find_one(core, name, p.get("where", "")))
    if not path:
        return Result(False, "не нашёл, что удалять")
    if core.safety.classify(path) == "forbidden":
        return Result(False, "это системная зона, удалять не буду")
    if not ctx.confirm(f"Отправляю в Корзину: {path}."):
        return Result(False, "оставил как есть")
    ok = recycle(path)
    return Result(ok, ("Убрал в Корзину: " + os.path.basename(path)) if ok else "Не получилось убрать в Корзину", path)


def h_rename(core, p, ctx):
    src = p["name"] if os.path.exists(p["name"]) else _find_one(core, p["name"], p.get("where", ""))
    if not src:
        return Result(False, "не нашёл, что переименовывать")
    dst = os.path.join(os.path.dirname(src), p["new"])
    for q in (src, dst):
        bad = _check_path(core, ctx, q, "Переименовываю")
        if bad:
            return bad
    if os.path.exists(dst) and not ctx.confirm(f"{os.path.basename(dst)} уже есть, заменить?"):
        return Result(False, "оставил как было")
    os.replace(src, dst)
    return Result(True, f"Теперь это «{os.path.basename(dst)}».")


def _copy_move(core, p, ctx, move):
    src = p["name"] if os.path.exists(p["name"]) else _find_one(core, p["name"])
    if not src:
        return Result(False, "не нашёл, что копировать")
    dest_dir = core.safety.place(p.get("dest", ""))
    dst = os.path.join(dest_dir, os.path.basename(src))
    for q in (src, dst):
        bad = _check_path(core, ctx, q, "Работаю с файлом")
        if bad:
            return bad
    if os.path.exists(dst) and not ctx.confirm(f"В {dest_dir} уже есть {os.path.basename(dst)}, заменить?"):
        return Result(False, "оставил как было")
    if os.path.isdir(src):
        if move:
            shutil.move(src, dst)
        else:
            shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        (shutil.move if move else shutil.copy2)(src, dst)
    return Result(True, ("Перенёс" if move else "Скопировал") + f" «{os.path.basename(src)}».")


def h_reveal(core, p, ctx):
    path = p.get("name") or (core.last_found[0] if getattr(core, "last_found", None) else "")
    if not path or not os.path.exists(path):
        return Result(False, "нечего показывать")
    subprocess.Popen(["explorer", "/select,", os.path.abspath(path)])
    return Result(True, "Показываю в проводнике.")


# ---------------------------------------------------------------- приложения и сайты
def h_app_open(core, p, ctx):
    target = (p.get("target") or "").strip().strip('"')
    if os.path.isfile(target):
        bad = _check_path(core, ctx, target, "Запускаю файл")
        if bad:
            return bad
        if core.safety.is_downloaded_exec(target) and not ctx.confirm(f"Запустить скачанный файл {target}."):
            return Result(False, "скачанное не запускаю без твоего «да»")
    msg = core.c_open(nlu.norm(target)) if not os.path.exists(target) else (os.startfile(target) or "Открываю.")
    ok = not str(msg).startswith(("Не нашёл", "Что открыть"))
    return Result(ok, msg if isinstance(msg, str) else "")


def h_app_close(core, p, ctx):
    msg = core.c_close(nlu.norm(p.get("target", "")))
    return Result(not str(msg).startswith(("Не вижу", "Что закрыть")), msg)


def h_web_open(core, p, ctx):
    url = (p.get("url") or "").strip()
    known = core.site_url(nlu.norm(url))
    url = known or (url if re.match(r"^https?://", url, re.I) else "https://" + url)
    webbrowser.open(url)
    return Result(True, "Открываю сайт.")


# ---------------------------------------------------------------- браузер (CDP)
def _b(core):
    return core.browser


def h_b_open_site(core, p, ctx):
    site = p.get("site", "")
    url = core.site_url(nlu.norm(site)) or ("https://" + site if "." in site else None)
    if not url:
        return Result(False, "не знаю такой сайт: " + site)
    tab = _b(core).open_url(url, new_tab=True)
    return Result(True, core.ph("open", x=site), tab.get("id"))


def h_b_search(core, p, ctx):
    url = "https://www.google.com/search?q=" + urllib.parse.quote(p["query"])
    _b(core).open_url(url, new_tab=True)
    return Result(True, core.ph("search", x=p["query"]))


def h_b_youtube(core, p, ctx):
    title = _b(core).youtube_play(p["query"])
    return Result(True, ("Включаю: " + title) if title else "Видео запущено.")


def h_b_seek(core, p, ctx):
    t = p.get("time") or {"mode": p.get("mode"), "sec": p.get("sec")}
    mode, sec = t["mode"], int(t["sec"] or 0)
    data = _b(core).seek(mode, sec)
    return Result(True, "Перемотал на " + nlu.fmt_time(data["t"]) + ("." if mode != "end" else ", почти в самый конец."))


def h_b_pause(core, p, ctx):
    return Result(True, "Продолжаю." if _b(core).toggle_pause() == "play" else "Пауза.")


def h_b_full(core, p, ctx):
    return Result(True, "Развернул." if _b(core).fullscreen() == "enter" else "Свернул обратно.")


def h_b_close_tab(core, p, ctx):
    if not _b(core).alive():
        return Result(False, "браузер Джарвиса не запущен")
    _b(core).close_tab()
    return Result(True, "Вкладку закрыл.")


def h_b_close(core, p, ctx):
    if not _b(core).alive():
        return Result(False, "браузер Джарвиса не запущен")
    _b(core).close_browser()
    return Result(True, "Браузер закрыл.")


def h_b_new_tab(core, p, ctx):
    _b(core).ensure()
    _b(core).open_url(p.get("url") or "about:blank")
    return Result(True, "Новая вкладка открыта.")


# ---------------------------------------------------------------- система
def _danger(fn):
    return fn


def h_sleep(core, p, ctx):
    subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=NO_WINDOW)
    return Result(True, "")


def h_shutdown(core, p, ctx):
    subprocess.run(["shutdown", "/s", "/t", "30", "/c", "Джарвис выключает компьютер"],
                   capture_output=True, creationflags=NO_WINDOW)
    return Result(True, "Выключу через тридцать секунд. Скажи «отмени выключение», если передумал.")


def h_restart(core, p, ctx):
    subprocess.run(["shutdown", "/r", "/t", "30", "/c", "Джарвис перезагружает компьютер"],
                   capture_output=True, creationflags=NO_WINDOW)
    return Result(True, "Перезагружу через тридцать секунд. Скажи «отмени перезагрузку», если передумал.")


def h_say(core, p, ctx):
    return Result(True, p.get("text", ""))


def h_legacy(core, p, ctx):
    """Обычная голосовая команда Джарвиса как шаг сценария («скажи время», «пошути»)."""
    reply = core.run_legacy(p.get("text", ""))
    return Result(True, reply or "")


def h_wait(core, p, ctx):
    end = time.time() + float(p.get("seconds", 1))
    while time.time() < end and not ctx.cancel.is_set():
        time.sleep(0.2)
    return Result(True, "")


def h_music(core, p, ctx):
    return Result(True, core.c_music(p.get("query", "")) or "")


# ---------------------------------------------------------------- извлечение параметров из речи
def x_open(rest, clause):
    t = rest.strip()
    if not t:
        return {}
    if len(t.split()) <= 3 and Registry and _site_probe(t):
        return {"_action": "browser.open_site", "site": t}
    return {"target": t}


_probe_core = [None]


def _site_probe(t):
    c = _probe_core[0]
    return bool(c and c.site_url(nlu.norm(t)))


def x_close(rest, clause):
    t = nlu.norm(rest)
    if t in WEB_CLOSE_BROWSER:
        return {"_action": "browser.close"}
    if t in WEB_CLOSE_TAB and _probe_core[0] and _probe_core[0].browser.alive():
        return {"_action": "browser.close_tab"}
    return {"target": rest.strip()} if rest.strip() else {}


def x_youtube(rest, clause):
    q = _strip_prep(rest)
    if nlu.norm(q) in ("", "нужное", "нужное видео", "любое", "какое нибудь", "какое-нибудь", "то что надо", "это"):
        return {}
    return {"query": re.sub(r"^(?:на\s+ютубе|на\s+youtube)\s*", "", q, flags=re.I)}


def x_seek(rest, clause):
    tc = nlu.parse_timecode(clause)
    return {"time": {"mode": tc[0], "sec": tc[1]}} if tc else {}


def x_name_where(rest, clause):
    name, where = split_where(rest)
    d = {"name": _cap(re.sub(r"^(?:с\s+названием|под\s+названием|которая\s+называется|называется)\s+", "", name,
                             flags=re.I))} if name else {}
    if where:
        d["where"] = where
    return d


def x_find(rest, clause):
    t = re.sub(r"^(?:файл|документ|папку|с\s+названием)\s+", "", rest.strip(), flags=re.I)
    name, where = split_where(t)
    d = {"name": name} if name else {}
    m = re.search(r"\s+(?:с\s+текстом|содержащий|где\s+написано)\s+(.+)$", name)
    if m:
        d = {"name": name[:m.start()].strip(), "contains": m.group(1).strip()}
    if where:
        d["where"] = where
    return d


def x_two(sep):
    def f(rest, clause):
        m = re.match(r"^(.+?)\s+" + sep + r"\s+(.+)$", rest.strip(), re.I)
        if not m:
            return {"name": rest.strip()} if rest.strip() else {}
        return {"name": m.group(1).strip(), "new": m.group(2).strip(), "dest": m.group(2).strip()}
    return f


def x_query(rest, clause):
    return {"query": rest.strip()} if rest.strip() else {}


def x_delete(rest, clause):
    t = re.sub(r"^(?:файл|папку|документ)\s+", "", rest.strip(), flags=re.I)
    name, where = split_where(t)
    return {"name": name, **({"where": where} if where else {})} if name else {}


def x_wait(rest, clause):
    tc = nlu.parse_timecode("на " + rest)
    return {"seconds": tc[1]} if tc and tc[1] else {}


# ---------------------------------------------------------------- регистрация
def register_all(core):
    reg = Registry(core)
    _probe_core[0] = core
    core.safety = Safety()
    A = reg.register
    P = Param
    A(Action("app.open", "Открыть программу", h_app_open, [P("target", "Какую программу открыть?", "app")], names=["открыть программу", "запустить приложение", "запустить программу"], undo="app.close"))
    A(Action("app.close", "Закрыть программу", h_app_close, [P("target", "Какую программу закрыть?", "app")], names=["закрыть программу", "закрыть приложение"]))
    A(Action("web.open", "Открыть сайт", h_web_open, [P("url", "Какой сайт открыть?", "site")], names=["открыть сайт", "зайти на сайт", "открыть страницу"]))
    A(Action("open.any", "Открыть", lambda c, p, x: h_app_open(c, p, x), [P("target", "Что открыть?")], extract=x_open))
    A(Action("close.any", "Закрыть", lambda c, p, x: h_app_close(c, p, x), [P("target", "Что закрыть?")], extract=x_close))
    A(Action("browser.open_site", "Открыть сайт в браузере Джарвиса", h_b_open_site, [P("site", "Какой сайт?", "site")], names=["открыть сайт в браузере"], undo="browser.close_tab", timeout=40))
    A(Action("browser.search", "Поиск в браузере", h_b_search, [P("query", "Что искать?")], names=["найти в интернете", "поискать в браузере"], extract=x_query, undo="browser.close_tab", timeout=40))
    A(Action("browser.youtube_play", "Включить видео на YouTube", h_b_youtube, [P("query", "Какое видео включить?", "query")], names=["включить видео", "включить видео на ютубе", "найти видео"], extract=x_youtube, undo="browser.close_tab", timeout=90))
    A(Action("browser.seek", "Перемотать видео", h_b_seek, [P("time", "На какое время перемотать? Например, на две минуты.", "time")], names=["перемотать видео", "перемотать"], extract=x_seek))
    A(Action("browser.pause", "Пауза или продолжить видео", h_b_pause, names=["поставить на паузу", "продолжить видео", "пауза"]))
    A(Action("browser.fullscreen", "Видео на весь экран", h_b_full, names=["на весь экран", "полноэкранный режим"]))
    A(Action("browser.close_tab", "Закрыть вкладку", h_b_close_tab, names=["закрыть вкладку"]))
    A(Action("browser.close", "Закрыть браузер Джарвиса", h_b_close, names=["закрыть браузер"]))
    A(Action("browser.new_tab", "Новая вкладка", h_b_new_tab, [P("url", "Какой адрес?", "site", False)], names=["новая вкладка", "открыть вкладку"]))
    A(Action("file.find", "Найти файл", h_find, [P("name", "Как называется файл?")], names=["найти файл", "поискать файл", "найти документ"], extract=x_find))
    A(Action("file.create_folder", "Создать папку", h_create_folder, [P("name", "Как назвать папку?")], names=["создать папку", "сделать папку"], extract=x_name_where))
    A(Action("file.create_file", "Создать файл", h_create_file, [P("name", "Как назвать файл?"), P("text", "Что в него записать?", "text", False)], names=["создать файл", "сделать текстовый файл"], extract=x_name_where))
    A(Action("file.open_folder", "Открыть папку", h_open_folder, [P("name", "Какую папку открыть?")], names=["открыть папку", "показать папку"], extract=x_name_where))
    A(Action("file.reveal", "Показать файл в проводнике", h_reveal, [P("name", "Какой файл показать?", "text", False)], names=["показать файл в проводнике"]))
    A(Action("file.delete", "Удалить в Корзину", h_delete, [P("name", "Что удалить?")], risk="danger", names=["удалить файл", "удалить папку"], extract=x_delete,
             describe=lambda p: "Удалить " + ("всё в рабочей папке" if nlu.norm(p.get("name", "")) in ("все", "всё") else p.get("name", "")), timeout=40))
    A(Action("file.rename", "Переименовать", h_rename, [P("name", "Какой файл?"), P("new", "Как назвать?")], names=["переименовать файл"], extract=x_two("в")))
    A(Action("file.copy", "Скопировать", lambda c, p, x: _copy_move(c, p, x, False), [P("name", "Что копировать?"), P("dest", "Куда? Например, на рабочий стол.")], names=["скопировать файл"], extract=x_two("(?:в|на)")))
    A(Action("file.move", "Переместить", lambda c, p, x: _copy_move(c, p, x, True), [P("name", "Что переместить?"), P("dest", "Куда?")], names=["переместить файл"], extract=x_two("(?:в|на)")))
    A(Action("music.play", "Включить музыку", h_music, [P("query", "Что включить?", "text", False)], names=["включить музыку", "поставить песню"], extract=x_query))
    A(Action("system.volume_up", "Громче", lambda c, p, x: Result(True, c.c_vol_up("")), names=["сделать громче", "прибавить звук"]))
    A(Action("system.volume_down", "Тише", lambda c, p, x: Result(True, c.c_vol_down("")), names=["сделать тише", "убавить звук"]))
    A(Action("system.mute", "Выключить звук", lambda c, p, x: Result(True, c.c_mute("")), names=["выключить звук"]))
    A(Action("system.screenshot", "Скриншот", lambda c, p, x: Result(True, c.c_screenshot("")), names=["сделать скриншот", "снимок экрана"]))
    A(Action("system.lock", "Заблокировать компьютер", lambda c, p, x: Result(True, c.c_lock("")), risk="danger", names=["заблокировать компьютер"], describe=lambda p: "Заблокировать компьютер"))
    A(Action("system.sleep", "Спящий режим", h_sleep, risk="danger", names=["перевести в сон", "спящий режим"], describe=lambda p: "Перевести компьютер в сон"))
    A(Action("system.shutdown", "Выключить компьютер", h_shutdown, risk="danger", names=["выключить компьютер"], describe=lambda p: "Выключить компьютер"))
    A(Action("system.restart", "Перезагрузить компьютер", h_restart, risk="danger", names=["перезагрузить компьютер"], describe=lambda p: "Перезагрузить компьютер"))
    A(Action("legacy", "Обычная команда", h_legacy, [P("text", "Какая команда?")]))
    A(Action("say", "Сказать фразу", h_say, [P("text", "Что сказать?")], names=["сказать", "сказать фразу", "произнести"]))
    A(Action("wait", "Подождать", h_wait, [P("seconds", "Сколько секунд ждать?", "number")], names=["подождать", "пауза в сценарии"], extract=x_wait, timeout=120))
    reg.load_phrases()
    return reg
