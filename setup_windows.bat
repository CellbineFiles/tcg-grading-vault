@echo off
cd /d "%~dp0"
if not exist "install_wizard.py" (
    echo.
    echo [!] Please extract the entire .zip folder first before running setup_windows.bat!
    echo     Right-click the .zip file -> Extract All... -> then run setup_windows.bat.
    echo.
    pause
    exit /b 1
)
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo [!] Python 3.10+ was not found on your PATH.
    echo Opening https://www.python.org/downloads/ so you can install it.
    echo IMPORTANT: Check "Add python.exe to PATH" during installation!
    echo.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)
where pythonw >nul 2>nul
if %errorlevel% equ 0 (
    start "" pythonw install_wizard.py
    exit /b 0
)
python install_wizard.py
if %errorlevel% neq 0 (
    echo.
    echo Setup encountered an issue. Review the message above.
    pause
)
