@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set PYTHONUTF8=1
set "PROJECT_DIR=%CD%"
set "EXPECTED_VENV=%PROJECT_DIR%\.venv"
set "PORT=%~1"

set "BOOTSTRAP="
where py >nul 2>nul
if %errorlevel%==0 (
  set "BOOTSTRAP=py -3"
) else (
  set "BOOTSTRAP=python"
)

call :ensure_venv
if errorlevel 1 goto :fail

set "PY=.venv\Scripts\python.exe"

echo [0/3] Closing old local web server if one is still running...
%PY% scripts\cleanup_local_web.py

if "%PORT%"=="" call :pick_port

if not exist "data\debug" mkdir "data\debug"
> "data\debug\current_local_port.txt" echo %PORT%
> "data\debug\current_local_url.txt" echo http://127.0.0.1:%PORT%

echo [1/3] Installing runtime packages. The first run can take a few minutes...
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [2/3] Installing browser component. The first run can take a few minutes...
%PY% -m playwright install chromium
if errorlevel 1 goto :fail

echo [3/3] Starting local web page...
echo Local URL: http://127.0.0.1:%PORT%
start "" http://127.0.0.1:%PORT%
%PY% scripts\run_local_web.py --port %PORT%
goto :eof

:ensure_venv
if not exist ".venv\Scripts\python.exe" (
  if exist ".venv" (
    echo [0/4] Existing runtime folder is incomplete. Backing it up first...
    call :backup_stale_venv
    if errorlevel 1 exit /b 1
  )
  echo [0/4] Creating local Python runtime...
  %BOOTSTRAP% -m venv .venv
  if errorlevel 1 exit /b 1
  call :stamp_venv
  exit /b 0
)

call :is_current_venv
if not errorlevel 1 (
  if not exist ".venv\resume_bot_project_root.txt" call :stamp_venv
  exit /b 0
)

echo [0/4] Existing runtime belongs to a different project folder.
echo        Expected: %EXPECTED_VENV%
echo        Backing it up to .venv.stale and creating a fresh one...
call :backup_stale_venv
if errorlevel 1 exit /b 1

echo [0/4] Creating local Python runtime...
%BOOTSTRAP% -m venv .venv
if errorlevel 1 exit /b 1
call :stamp_venv
exit /b 0

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

:backup_stale_venv
if not exist ".venv" exit /b 0
if exist ".venv.stale" (
  echo Backup folder .venv.stale already exists.
  echo Rename or delete it, then start again.
  pause
  exit /b 1
)
ren ".venv" ".venv.stale" >nul 2>nul
if errorlevel 1 (
  echo Failed to back up old runtime. Close any running windows and try again.
  pause
  exit /b 1
)
echo Old runtime was backed up to .venv.stale.
exit /b 0

:stamp_venv
> ".venv\resume_bot_project_root.txt" echo %PROJECT_DIR%
exit /b 0

:pick_port
for /l %%P in (8765,1,8785) do (
  call :check_port %%P
  if not errorlevel 1 (
    set "PORT=%%P"
    goto :eof
  )
)
echo No free local port was found from 8765 to 8785.
echo Close other local web tools and try again.
pause
exit /b 1

:check_port
netstat -ano | findstr ":%1" >nul
if %errorlevel%==0 exit /b 1
exit /b 0

:fail
echo Startup failed.
echo Please check the message above, or open docs\TROUBLESHOOTING.md.
pause
exit /b 1
