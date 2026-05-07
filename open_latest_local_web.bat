@echo off
setlocal
cd /d %~dp0

if not exist "data\debug\current_local_port.txt" (
  echo current_local_port.txt not found. Run quick_start_local.bat first.
  pause
  exit /b 1
)

set /p PORT=<"data\debug\current_local_port.txt"
if "%PORT%"=="" (
  echo No current port recorded. Run quick_start_local.bat first.
  pause
  exit /b 1
)

echo Opening http://127.0.0.1:%PORT%
start "" http://127.0.0.1:%PORT%
