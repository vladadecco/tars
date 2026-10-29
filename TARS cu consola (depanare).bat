@echo off
chcp 65001 >nul
cd /d "%~dp0"
set JARVIS_DEBUG=1
".venv\Scripts\python.exe" main.py
echo.
echo TARS s-a oprit. Jurnalul complet e in date\tars.log
pause
