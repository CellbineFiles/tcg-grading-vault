@echo off
cd /d "%~dp0"
if not exist ".venv" (
    call setup_windows.bat
    exit /b 0
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" run.py
) else (
    call .venv\Scripts\activate.bat
    python run.py
    pause
)
