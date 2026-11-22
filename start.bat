@echo off
rem Starts Scripture Screen with no black window, so nobody can close the app by closing that window.
rem If the app will not open, use start_with_messages.bat to see why.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" goto messages
start "" ".venv\Scripts\pythonw.exe" -m verse_screen %*
exit /b
:messages
.venv\Scripts\python.exe -m verse_screen %*
if errorlevel 1 pause
