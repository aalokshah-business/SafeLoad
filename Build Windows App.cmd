@echo off
setlocal
cd /d "%~dp0"
python -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller==6.16.0
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onedir --name SafeLoad --collect-all pymupdf app.py
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\package_release.py --version 1.0.1
if errorlevel 1 goto failed
echo Built dist\SafeLoad-Windows-x64.zip. Extract the ENTIRE ZIP on the office computer.
pause
exit /b 0
:failed
echo Build failed. Check errors above. See README.md for offline setup.
pause
exit /b 1
