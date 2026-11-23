@echo off
title Smart Retail
cd /d "%~dp0"

REM change this if mysql is somewhere else
set MYSQL_BIN=C:\xampp\mysql\bin

if not exist .env copy .env.example .env >nul

REM make the venv the first time
if not exist venv\Scripts\python.exe (
  echo Setting up venv, please wait...
  python -m venv venv
  venv\Scripts\python -m pip install -q -r requirements.txt
)

REM start mysql if nothing is on port 3306
netstat -an | findstr ":3306" | findstr "LISTENING" >nul
if errorlevel 1 (
  echo Starting MySQL...
  start "MySQL" /min "%MYSQL_BIN%\mysqld.exe" --defaults-file="%MYSQL_BIN%\my.ini" --standalone
  timeout /t 5 /nobreak >nul
)

start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:5000"

venv\Scripts\python app.py
pause
