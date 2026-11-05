# -*- coding: utf-8 -*-
"""Своя иконка вместо иконки Python: AppUserModelID (значок на панели задач), WM_SETICON (окно), трей."""
import ctypes
import threading
import time
from ctypes import wintypes

from core.paths import icon_path
from core.logsetup import get

log = get("icon")
APP_ID = "Jarvis.VoiceAssistant.3"
WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
GCLP_HICON, GCLP_HICONSM = -14, -34


def set_app_id(app_id=APP_ID):
    """Вызывать ДО создания окна: тогда Windows не группирует нас с pythonw.exe и берёт нашу иконку."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except Exception:
        log.exception("AppUserModelID не задан")
        return False


def _load_icon(path, size):
    u = ctypes.windll.user32
    u.LoadImageW.restype = wintypes.HANDLE
    u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int,
                             ctypes.c_int, wintypes.UINT]
    return u.LoadImageW(None, path, IMAGE_ICON, size, size, LR_LOADFROMFILE)


def apply_to_window(title="Джарвис"):
    """Ставит иконку на окно с заголовком title. Возвращает True, если окно найдено."""
    path = icon_path()
    if not path:
        return False
    u = ctypes.windll.user32
    u.FindWindowW.restype = wintypes.HWND
    u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    hwnd = u.FindWindowW(None, title)
    if not hwnd:
        return False
    big = _load_icon(path, u.GetSystemMetrics(11))     # SM_CXICON
    small = _load_icon(path, u.GetSystemMetrics(49))   # SM_CXSMICON
    u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.SendMessageW.restype = ctypes.c_ssize_t
    if big:
        u.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
    if small:
        u.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
    try:
        f = u.SetClassLongPtrW
        f.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        f.restype = ctypes.c_void_p
        if big:
            f(hwnd, GCLP_HICON, big)
        if small:
            f(hwnd, GCLP_HICONSM, small)
    except Exception:
        pass
    return bool(big or small)


def apply_when_ready(title="Джарвис", seconds=30):
    """Фоновый поток: ждёт появления окна и ставит иконку сразу, а потом ещё раз (окно могут перерисовать)."""
    def run():
        end = time.time() + seconds
        hits = 0
        while time.time() < end and hits < 3:
            try:
                if apply_to_window(title):
                    if hits == 0:
                        try:
                            from core.startup import mark
                            mark("иконка применена к окну")
                        except Exception:
                            pass
                    hits += 1
                    time.sleep(1.5)
                    continue
            except Exception:
                log.exception("WM_SETICON")
            time.sleep(0.15)
    threading.Thread(target=run, daemon=True).start()


def tray_image(size=64):
    """Картинка для трея: берётся наш .ico, а не заглушка."""
    p = icon_path()
    try:
        from PIL import Image
        if p:
            im = Image.open(p)
            im.load()
            return im.convert("RGBA").resize((size, size))
    except Exception:
        log.exception("иконка трея")
    from brand import make_icon_image
    return make_icon_image(size)
