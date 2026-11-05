# -*- coding: utf-8 -*-
"""Assistant.exe: маленький запускатор. Находит runtime\\python\\pythonw.exe рядом с собой и стартует jarvis.py без консоли.
Иконка .exe задаётся при сборке (build_exe.bat, --icon=assets/app.ico)."""
import ctypes
import os
import subprocess
import sys


def main():
    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    pyw = os.path.join(here, "runtime", "python", "pythonw.exe")
    script = os.path.join(here, "jarvis.py")
    if not (os.path.isfile(pyw) and os.path.isfile(script)):
        ctypes.windll.user32.MessageBoxW(0, "Джарвис не установлен. Запустите INSTALL_JARVIS.bat.", "Джарвис", 0x10)
        return
    subprocess.Popen([pyw, script], cwd=here, creationflags=0x08000000)       # CREATE_NO_WINDOW


if __name__ == "__main__":
    main()
