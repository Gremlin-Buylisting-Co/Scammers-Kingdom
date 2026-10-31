@echo off
rem Builds ScammersKingdom.exe (one file, with a console window) using PyInstaller.
rem Run this from the folder that holds server.py.
setlocal
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
  echo Python 3 was not found. Install it first, then run this file again.
  exit /b 1
)

python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo PyInstaller is not installed. Install it with:  python -m pip install pyinstaller
  exit /b 1
)

set ICON=
if exist "icon.ico" set ICON=--icon "icon.ico"

python -m PyInstaller --noconfirm --clean --onefile --console --name ScammersKingdom ^
  --add-data "static;static" %ICON% server.py
if errorlevel 1 (
  echo The build failed.
  exit /b 1
)

echo.
echo Done. The program is dist\ScammersKingdom.exe
endlocal
