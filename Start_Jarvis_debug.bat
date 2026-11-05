@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0runtime\python\python.exe" jarvis.py
pause
