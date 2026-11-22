@echo off
cd /d "%~dp0"
set "PATH=%LOCALAPPDATA%\Programs\Python\Launcher;%PATH%"
py -3.12 --version >nul 2>nul
if errorlevel 1 (
  echo Installing Python 3.12...
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
)
py -3.12 install.py
if errorlevel 1 (
  echo.
  echo Setup failed. Install Python 3.12 from python.org and run this again.
)
pause
