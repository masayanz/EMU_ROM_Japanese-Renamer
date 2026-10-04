@echo off
cd /d "%~dp0"
py rom_renamer_gui.py
if errorlevel 1 (
  echo.
  echo An error occurred. Press any key to close.
  pause >nul
)
