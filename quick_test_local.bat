@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set PYTHONUTF8=1

cd /d %~dp0
set "PROJECT_DIR=%CD%"
set "EXPECTED_VENV=%PROJECT_DIR%\.venv"

if not exist ".venv\Scripts\python.exe" (
  echo Local virtualenv not found. Run quick_start_local.bat first.
  pause
  exit /b 1
)

call :is_current_venv
if errorlevel 1 (
  echo Local virtualenv was created for another project path.
  echo Run quick_start_local.bat to back it up and recreate it for:
  echo %EXPECTED_VENV%
  pause
  exit /b 1
)

set "ARGS="
if "%1"=="--live" set "ARGS=--live"

.venv\Scripts\python.exe scripts\local_self_test.py %ARGS%
if errorlevel 1 (
  echo Self test failed.
  pause
  exit /b 1
)

pause
goto :eof

:is_current_venv
if not exist ".venv" exit /b 1

if exist ".venv\resume_bot_project_root.txt" (
  set "STORED_ROOT="
  set /p STORED_ROOT=<".venv\resume_bot_project_root.txt"
  if /i "!STORED_ROOT!"=="%PROJECT_DIR%" exit /b 0
)

if exist ".venv\pyvenv.cfg" (
  findstr /c:"%EXPECTED_VENV%" ".venv\pyvenv.cfg" >nul 2>nul
  if not errorlevel 1 exit /b 0
)

if exist ".venv\Scripts\activate.bat" (
  findstr /c:"%EXPECTED_VENV%" ".venv\Scripts\activate.bat" >nul 2>nul
  if not errorlevel 1 exit /b 0
)

exit /b 1
