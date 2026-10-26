@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\SafeLoad\SafeLoad.exe" (
    start "" "%~dp0dist\SafeLoad\SafeLoad.exe"
    exit /b
)
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app.py"
    exit /b
)
python app.py
if errorlevel 1 (
    echo Install dependencies first. See README.md for setup.
    pause
)
