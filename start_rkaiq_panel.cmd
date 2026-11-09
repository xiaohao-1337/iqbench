@echo off
setlocal
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo Create .venv and install requirements.txt first. See README.md.
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -B "%~dp0launch.py"
