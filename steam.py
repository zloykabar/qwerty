# -*- coding: utf-8 -*-
"""Список установленных игр Steam (для команды «запусти игру ...»)."""
import glob
import os
import re

SKIP = re.compile(r"(?i)(steamworks|redistributable|proton|steam linux runtime|steamvr|wallpaper engine$)")


def steam_root():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return winreg.QueryValueEx(k, "SteamPath")[0].replace("/", "\\")
    except Exception:
        for p in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
            if os.path.isdir(p):
                return p
    return None


def library_dirs():
    root = steam_root()
    if not root:
        return []
    libs = [os.path.join(root, "steamapps")]
    try:
        with open(os.path.join(root, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace") as f:
            for m in re.finditer(r'"path"\s+"([^"]+)"', f.read()):
                libs.append(os.path.join(m.group(1).replace("\\\\", "\\"), "steamapps"))
    except Exception:
        pass
    seen, out = set(), []
    for d in libs:
        if d.lower() not in seen and os.path.isdir(d):
            seen.add(d.lower())
            out.append(d)
    return out


def installed_games():
    """[(appid, название)]"""
    games = []
    for lib in library_dirs():
        for path in glob.glob(os.path.join(lib, "appmanifest_*.acf")):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    t = f.read()
                appid = re.search(r'"appid"\s+"(\d+)"', t).group(1)
                name = re.search(r'"name"\s+"([^"]+)"', t).group(1)
                if not SKIP.search(name):
                    games.append((appid, name))
            except Exception:
                continue
    return games
