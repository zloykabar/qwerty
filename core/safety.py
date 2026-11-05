# -*- coding: utf-8 -*-
"""Безопасность: разрешённые каталоги, запрещённые пути, подтверждения, запуск процессов без shell."""
import ctypes
import os
import shlex
import subprocess

from core.paths import BASE_DIR, load_json
from core.logsetup import get

log = get("safety")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _shell_folder(csidl, fallback):
    try:
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), fallback)


def known_folders():
    home = os.path.expanduser("~")
    documents = _shell_folder(0x0005, "Documents")
    return {
        "desktop": _shell_folder(0x0000, "Desktop"),
        "documents": documents,
        "downloads": os.path.join(home, "Downloads"),
        "pictures": _shell_folder(0x0027, "Pictures"),
        "workdir": os.path.join(documents, "Jarvis"),
        "home": home,
    }


def _norm_path(p):
    return os.path.normcase(os.path.abspath(os.path.expandvars(p)))


class Safety:
    def __init__(self):
        self.cfg = load_json("security.json", {}) or {}
        self.folders = known_folders()
        self.roots = [_norm_path(self.folders[k]) for k in self.cfg.get("allowed_roots", ["workdir"])
                      if k in self.folders]
        self.forbidden = []
        for f in self.cfg.get("forbidden", []):
            p = os.path.expandvars(f.replace("{BASE}", BASE_DIR))
            if "%" not in p:
                self.forbidden.append(_norm_path(p))
        self.exec_ext = tuple(self.cfg.get("exec_extensions", []))
        self.yes = set(self.cfg.get("yes_words", ["да"]))
        self.confirm_timeout = int(self.cfg.get("confirm_timeout", 20))
        os.makedirs(self.folders["workdir"], exist_ok=True)

    @staticmethod
    def _inside(p, root):
        return p == root or p.startswith(root.rstrip("\\/") + os.sep)

    def classify(self, path):
        """'forbidden' | 'ok' | 'confirm' (за пределами разрешённых каталогов)."""
        p = _norm_path(path)
        if os.path.dirname(p) == p:                                   # корень диска
            return "forbidden"
        if any(self._inside(p, f) for f in self.forbidden):
            return "forbidden"
        if any(self._inside(p, r) for r in self.roots):
            return "ok"
        return "confirm"

    def is_downloaded_exec(self, path):
        """Скачанный исполняемый файл: лежит в «Загрузках» или помечен Windows как загруженный из интернета."""
        if not path.lower().endswith(self.exec_ext):
            return False
        p = _norm_path(path)
        if self._inside(p, _norm_path(self.folders["downloads"])):
            return True
        try:
            with open(path + ":Zone.Identifier", "rb") as f:
                return b"ZoneId" in f.read()
        except Exception:
            return False

    def place(self, word):
        """«рабочий стол», «документы», «загрузки» -> путь."""
        w = (word or "").lower().replace("ё", "е")
        for keys, name in ((("рабоч", "стол"), "desktop"), (("документ",), "documents"), (("загрузк",), "downloads"),
                           (("изображен", "картинк", "фото"), "pictures")):
            if any(k in w for k in keys):
                return self.folders[name]
        return self.folders["workdir"]

    # ---- процессы: только список аргументов, никакого shell=True ----
    @staticmethod
    def split_command(cmd):
        return shlex.split(cmd, posix=False)

    def popen(self, cmd, **kw):
        args = cmd if isinstance(cmd, (list, tuple)) else self.split_command(cmd)
        args = [a.strip('"') for a in args]
        log.info("запуск процесса: %s", args)
        return subprocess.Popen(args, creationflags=NO_WINDOW, shell=False, **kw)


def recycle(path):
    """Удаление в Корзину (можно восстановить). Возвращает True/False."""
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _pack_ = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 8
        _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = 3                                   # FO_DELETE
    op.pFrom = os.path.abspath(path) + "\0"        # двойной нуль в конце добавит ctypes для wchar_p
    op.pTo = None
    op.fFlags = 0x40 | 0x10 | 0x4 | 0x400          # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
    try:
        return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted
    except Exception:
        log.exception("recycle")
        return False
