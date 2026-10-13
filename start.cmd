@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".runtime\ready" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
  if errorlevel 1 (pause & exit /b 1)
)
".venv\Scripts\python.exe" launcher.py
if errorlevel 1 pause
