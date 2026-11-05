@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Installing Whisper (accurate speech recognition). 10-30 minutes...
"%~dp0runtime\python\python.exe" install_whisper.py
pause
