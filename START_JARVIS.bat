@echo off
cd /d "%~dp0"
if not exist "%~dp0runtime\python\pythonw.exe" (
  echo Jarvis is not installed yet. Run INSTALL_JARVIS.bat first.
  pause
  exit /b 1
)
wscript "%~dp0Jarvis.vbs"
