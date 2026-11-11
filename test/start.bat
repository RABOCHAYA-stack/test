@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

if not exist data mkdir data

rem Открыть браузер через 2 секунды, пока сервер запускается
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8000'"

where py >nul 2>nul
if errorlevel 1 (
    python -X utf8 app.py
) else (
    py -X utf8 app.py
)

pause
