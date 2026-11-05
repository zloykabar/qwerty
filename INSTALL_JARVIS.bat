@echo off
chcp 65001 >nul
title Jarvis - installer
cd /d "%~dp0"
if not exist "%~dp0install.ps1" (
  echo install.ps1 not found. Unpack the whole archive first, then run this file again.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 pause
