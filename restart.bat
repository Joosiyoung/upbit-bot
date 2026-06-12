@echo off
rem Restart script - ASCII only (Korean text breaks cmd codepage parsing)
cd /d "%~dp0"

set PYTHON=C:\Users\jso84\AppData\Local\Python\pythoncore-3.14-64\python.exe

echo [1/3] Stopping existing app.py processes...
"%PYTHON%" scripts\restart_helper.py kill

echo [2/3] Resetting server.log...
if not exist logs mkdir logs
if exist logs\server.log del /f logs\server.log >nul 2>&1

echo [3/3] Starting server...
start "" /B "%PYTHON%" app.py 1>logs\server.log 2>&1

rem Wait ~4s (ping trick works in non-interactive shells where timeout fails)
ping -n 5 127.0.0.1 >nul

"%PYTHON%" scripts\restart_helper.py check
