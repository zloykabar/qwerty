@echo off
chcp 65001 >nul
title Jarvis - diagnostics
cd /d "%~dp0"
if exist "%~dp0runtime\python\python.exe" (
  "%~dp0runtime\python\python.exe" diagnose.py
) else (
  echo Jarvis is not installed yet. Run INSTALL_JARVIS.bat first.
)
pause
