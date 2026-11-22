@echo off
rem Opens Scripture Screen with a black window that shows any error, for when the desktop icon does nothing.
cd /d "%~dp0"
python\python.exe -E -s -m verse_screen %*
if errorlevel 1 pause
