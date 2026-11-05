# -*- coding: utf-8 -*-
"""Управление Chrome/Edge через Chrome DevTools Protocol (CDP).
Почему CDP: он работает с настоящими вкладками, умеет искать видео, перематывать и закрывать конкретную вкладку,
не угадывая координаты кликов. Требование: браузер должен быть запущен с --remote-debugging-port. Джарвис запускает
его САМ в своём профиле (папка browser_profile), поэтому в твоём обычном окне ничего не трогается.
Нужна библиотека websocket-client."""
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request

from core.logsetup import get
from core.paths import user_path

log = get("browser")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class BrowserError(Exception):
    pass


class Browser:
    def __init__(self, cfg=None):
        cfg = cfg or {}
        self.port = int(cfg.get("browser_port", 9222))
        self.pref = str(cfg.get("browser", "auto")).lower()          # auto | chrome | edge
        self.profile = user_path("browser_profile")
        self.current = None                                          # id вкладки, которую открыл Джарвис

    # ------------------------------------------------------------ запуск
    def find_exe(self):
        bases = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        chrome = [os.path.join(b, "Google", "Chrome", "Application", "chrome.exe") for b in bases if b]
        edge = [os.path.join(b, "Microsoft", "Edge", "Application", "msedge.exe") for b in bases if b]
        order = edge + chrome if self.pref == "edge" else chrome + edge
        if self.pref == "chrome":
            order = chrome + edge
        for p in order:
            if os.path.isfile(p):
                return p
        try:
            import winreg
            for name in ("chrome.exe", "msedge.exe"):
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + name) as k:
                        p = winreg.QueryValueEx(k, "")[0]
                        if os.path.isfile(p):
                            return p
                except OSError:
                    pass
        except ImportError:
            pass
        return None

    def _http(self, path, method="GET", timeout=3):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")

    def alive(self):
        try:
            self._http("/json/version", timeout=1.5)
            return True
        except Exception:
            return False

    def ensure(self, url="about:blank", wait=20):
        """Запускает браузер с отладочным портом (если ещё не запущен) и ждёт готовности."""
        if self.alive():
            return True
        exe = self.find_exe()
        if not exe:
            raise BrowserError("не нашёл Chrome или Edge, установи один из них")
        os.makedirs(self.profile, exist_ok=True)
        args = [exe, f"--remote-debugging-port={self.port}", "--remote-allow-origins=*",
                f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check", url]
        log.info("запуск браузера: %s", args)
        subprocess.Popen(args, shell=False)
        end = time.time() + wait
        while time.time() < end:
            if self.alive():
                return True
            time.sleep(0.4)
        raise BrowserError("браузер не ответил на отладочный порт")

    # ------------------------------------------------------------ вкладки
    def tabs(self):
        data = json.loads(self._http("/json/list"))
        return [t for t in data if t.get("type") == "page"]

    def new_tab(self, url="about:blank"):
        raw = self._http("/json/new?" + urllib.parse.quote(url, safe=":/?&=%#@+,;"), method="PUT", timeout=8)
        tab = json.loads(raw)
        self.current = tab["id"]
        return tab

    def tab(self):
        """Текущая вкладка Джарвиса; если её нет, берём первую или создаём новую."""
        tabs = self.tabs()
        for t in tabs:
            if t["id"] == self.current:
                return t
        if tabs:
            self.current = tabs[0]["id"]
            return tabs[0]
        return self.new_tab()

    def activate(self, tab):
        try:
            self._http("/json/activate/" + tab["id"])
        except Exception:
            pass

    def close_tab(self, tab=None):
        tab = tab or self.tab()
        self._http("/json/close/" + tab["id"])
        if self.current == tab["id"]:
            self.current = None

    def close_browser(self):
        info = json.loads(self._http("/json/version"))
        ws_url = info.get("webSocketDebuggerUrl")
        if not ws_url:
            raise BrowserError("нет адреса управления браузером")
        self._ws_call(ws_url, "Browser.close", {}, 5)
        self.current = None

    # ------------------------------------------------------------ CDP
    @staticmethod
    def _ws_call(ws_url, method, params, timeout):
        try:
            import websocket
        except ImportError:
            raise BrowserError("не хватает библиотеки websocket-client, запусти INSTALL_JARVIS.bat ещё раз")
        try:
            ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
        except Exception as e:
            raise BrowserError("нет связи с браузером: " + str(e))
        try:
            ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
            end = time.time() + timeout
            while time.time() < end:
                try:
                    msg = json.loads(ws.recv())
                except Exception:
                    break
                if msg.get("id") == 1:
                    if "error" in msg:
                        raise BrowserError(msg["error"].get("message", "ошибка CDP"))
                    return msg.get("result", {})
        finally:
            try:
                ws.close()
            except Exception:
                pass
        return {}

    def cdp(self, tab, method, params=None, timeout=10):
        return self._ws_call(tab["webSocketDebuggerUrl"], method, params, timeout)

    def evaluate(self, tab, expr, gesture=False, timeout=10):
        res = self.cdp(tab, "Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True,
                                                  "userGesture": gesture}, timeout)
        if res.get("exceptionDetails"):
            raise BrowserError("ошибка скрипта на странице")
        return (res.get("result") or {}).get("value")

    def wait_for(self, tab, cond_js, timeout=15, step=0.4):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.evaluate(tab, cond_js):
                    return True
            except BrowserError:
                pass
            time.sleep(step)
        return False

    def navigate(self, url, tab=None, timeout=20):
        tab = tab or self.tab()
        self.cdp(tab, "Page.navigate", {"url": url})
        self.wait_for(tab, "document.readyState==='complete'||document.readyState==='interactive'", timeout)
        return self.refresh(tab)

    def refresh(self, tab):
        for t in self.tabs():
            if t["id"] == tab["id"]:
                return t
        return tab

    # ------------------------------------------------------------ высокоуровневые действия
    def open_url(self, url, new_tab=True):
        self.ensure()
        tab = self.new_tab(url) if new_tab else self.navigate(url)
        self.activate(tab)
        self.wait_for(self.refresh(tab), "document.readyState==='complete'||document.readyState==='interactive'", 15)
        return self.refresh(tab)

    CONSENT_JS = ("(function(){const re=/(accept all|tout accepter|alle akzeptieren|aceptar todo|принять все|"
                  "accetta tutto|i agree|ich stimme zu)/i;"
                  "const b=[...document.querySelectorAll('button,[role=button]')].find(x=>re.test("
                  "(x.innerText||x.getAttribute('aria-label')||'')));if(b){b.click();return true}return false})()")

    def dismiss_consent(self, tab):
        try:
            if "consent." in (self.refresh(tab).get("url") or "") or self.evaluate(
                    tab, "!!document.querySelector('form[action*=\"consent\"]')"):
                self.evaluate(tab, self.CONSENT_JS, gesture=True)
                time.sleep(1.5)
        except BrowserError:
            pass

    def youtube_play(self, query):
        """Поиск на YouTube -> первое видео -> воспроизведение. Возвращает название видео."""
        self.ensure()
        tab = self.tab()
        url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query) + "&sp=EgIQAQ%253D%253D"
        tab = self.navigate(url, tab)
        self.dismiss_consent(tab)
        tab = self.refresh(tab)
        sel = "ytd-video-renderer a#video-title"
        if not self.wait_for(tab, f"!!document.querySelector('{sel}')", 15):
            self.dismiss_consent(tab)
            if not self.wait_for(tab, f"!!document.querySelector('{sel}')", 10):
                raise BrowserError("на YouTube не нашлось видео по запросу")
        href = self.evaluate(tab, f"document.querySelector('{sel}').href")
        title = self.evaluate(tab, f"(document.querySelector('{sel}').getAttribute('title')||'').slice(0,80)") or ""
        if not href:
            raise BrowserError("не получил ссылку на видео")
        tab = self.navigate(href, tab)
        if not self.wait_for(tab, "!!document.querySelector('video')&&document.querySelector('video').readyState>=2", 20):
            raise BrowserError("видео не загрузилось")
        self.evaluate(tab, "(function(){const v=document.querySelector('video');v.play&&v.play();return 1})()",
                      gesture=True)
        self.skip_ad(tab)
        return title

    def skip_ad(self, tab, seconds=6):
        end = time.time() + seconds
        js = ("(function(){const b=document.querySelector('.ytp-skip-ad-button,.ytp-ad-skip-button-modern,"
              ".ytp-ad-skip-button');if(b){b.click();return 1}return 0})()")
        while time.time() < end:
            try:
                if self.evaluate(tab, js, gesture=True):
                    return True
            except BrowserError:
                return False
            time.sleep(0.8)
        return False

    def seek(self, mode, sec):
        tab = self.tab()
        js = ("(function(mode,sec){const v=document.querySelector('video');"
              "if(!v)return JSON.stringify({ok:false});const d=v.duration;let t;"
              "if(mode==='end')t=isFinite(d)?d-0.5:0;else if(mode==='rel')t=v.currentTime+sec;else t=sec;"
              "if(isFinite(d))t=Math.max(0,Math.min(t,d-0.3));else t=Math.max(0,t);"
              "v.currentTime=t;return JSON.stringify({ok:true,t:t,d:isFinite(d)?d:0})})(%s,%s)"
              % (json.dumps(mode), json.dumps(sec)))
        data = json.loads(self.evaluate(tab, js) or "{}")
        if not data.get("ok"):
            raise BrowserError("на странице нет видео")
        return data

    def toggle_pause(self):
        tab = self.tab()
        res = self.evaluate(tab, "(function(){const v=document.querySelector('video');if(!v)return 'none';"
                                 "if(v.paused){v.play();return 'play'}v.pause();return 'pause'})()", gesture=True)
        if res == "none":
            raise BrowserError("на странице нет видео")
        return res

    def fullscreen(self):
        tab = self.tab()
        try:
            ok = self.evaluate(tab, "(function(){const p=document.querySelector('#movie_player')||"
                                    "document.querySelector('video');if(!p)return false;"
                                    "if(document.fullscreenElement){document.exitFullscreen();return 'exit'}"
                                    "p.requestFullscreen();return 'enter'})()", gesture=True)
        except BrowserError:
            ok = False
        if not ok:
            raise BrowserError("не получилось развернуть на весь экран")
        return ok
