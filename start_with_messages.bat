@echo off
rem For fault-finding only: starts the app with the black window that shows its messages.
cd /d "%~dp0"
.venv\Scripts\python.exe -m verse_screen %*
pause
