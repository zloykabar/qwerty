@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "runtime\python\python.exe" (echo Run INSTALL_JARVIS.bat first. & pause & exit /b 1)
echo Building Assistant.exe (small launcher with the Jarvis icon)...
"runtime\python\python.exe" -m pip install --disable-pip-version-check pyinstaller
"runtime\python\python.exe" -m PyInstaller --noconfirm --clean --onefile --noconsole --icon="assets/app.ico" --name="Assistant" --add-data "assets/app.ico;assets" launcher.py
if exist "dist\Assistant.exe" (copy /y "dist\Assistant.exe" "Assistant.exe" >nul & echo Done: Assistant.exe) else (echo Build failed, see messages above.)
pause
