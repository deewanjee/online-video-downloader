@echo off
setlocal
cd /d "%~dp0"
title AAASoft Video Studio - Keep this window open
if not exist "requirements.lock" (
  echo Project files are missing. Extract the entire ZIP before launching.
  pause
  exit /b 1
)
set "SV_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%SV_PY%" if exist "%~dp0..\.venv\Scripts\python.exe" set "SV_PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%SV_PY%" (
  echo Creating the Python environment...
  py -3.12 -m venv .venv
  if errorlevel 1 (
    echo Install Python 3.12 first, then launch again.
    pause
    exit /b 1
  )
)
"%SV_PY%" -m app.dependencies >nul 2>&1
if errorlevel 1 (
  echo Preparing or updating required dependencies. Internet access is needed for this update...
  "%SV_PY%" -m pip install -r requirements.lock --timeout 120 --retries 10
  if errorlevel 1 (
    echo Installation failed. Check the error above and try again.
    pause
    exit /b 1
  )
)
"%SV_PY%" -m app.desktop
if errorlevel 1 pause
