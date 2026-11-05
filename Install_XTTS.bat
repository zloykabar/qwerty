@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Installing XTTS (voice cloning). 10-30 minutes...
"%~dp0runtime\python\python.exe" install_xtts.py
pause
