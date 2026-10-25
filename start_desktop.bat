@echo off
chcp 65001 >nul
title AI-LPR Desktop Live View
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [AI-LPR] Run start.bat once first to create the Python environment.
  pause
  exit /b 1
)
netstat -ano | findstr /R /C:":8080 .*LISTENING" >nul
if not errorlevel 1 (
  echo.
  echo [AI-LPR] Port 8080 is already in use - close the start.bat window first.
  echo          ^(the desktop view runs the server itself so LPR and the live view share one decoder^)
  echo.
  pause
  exit /b 0
)
echo.
echo   AI-LPR Desktop Live View  +  web  http://localhost:8080
echo   Usage: start_desktop.bat [--cams 1,2]
echo.
".venv\Scripts\python.exe" -m app.desktop --port 8080 %*
pause
