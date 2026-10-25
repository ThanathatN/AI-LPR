@echo off
chcp 65001 >nul
title AI-LPR Command Center
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [AI-LPR] Creating Python environment...
  python -m venv .venv || goto :err
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :err
)
netstat -ano | findstr /R /C:":8080 .*LISTENING" >nul
if not errorlevel 1 (
  echo.
  echo [AI-LPR] Port 8080 is already in use - AI-LPR is probably already running.
  echo          Open http://localhost:8080  or close the other window first.
  echo.
  pause
  exit /b 0
)
echo.
echo   AI-LPR Command Center
echo   Open:  http://localhost:8080   (or http://192.168.1.40:8080 from the LAN)
echo.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --no-access-log
pause
exit /b 0
:err
echo [AI-LPR] Setup failed. Make sure Python 3.10+ is installed and the PC has internet access for the first run.
pause
