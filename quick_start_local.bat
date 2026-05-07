@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set PYTHONUTF8=1

cd /d %~dp0
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

echo [0/3] 正在关闭上一次残留的本地服务...
%PY% scripts\cleanup_local_web.py

if "%PORT%"=="" call :pick_port

if not exist "data\debug" mkdir "data\debug"
> "data\debug\current_local_port.txt" echo %PORT%
> "data\debug\current_local_url.txt" echo http://127.0.0.1:%PORT%

echo [1/3] 正在安装运行依赖，第一次会比较慢...
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [2/3] 正在安装浏览器组件，第一次会比较慢...
%PY% -m playwright install chromium
if errorlevel 1 goto :fail

echo [3/3] 正在启动本地网页...
echo Local URL: http://127.0.0.1:%PORT%
start "" http://127.0.0.1:%PORT%
%PY% scripts\run_local_web.py --port %PORT%
goto :eof

:ensure_venv
if not exist ".venv\Scripts\python.exe" (
  if exist ".venv" (
    echo [0/4] 发现旧运行环境不完整，正在备份并重建...
    call :backup_stale_venv
    if errorlevel 1 exit /b 1
  )
  echo [0/4] 正在创建本地运行环境...
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

echo [0/4] 发现旧运行环境属于另一个项目目录。
echo        当前需要：%EXPECTED_VENV%
echo        正在备份到 .venv.stale 并重新创建...
call :backup_stale_venv
if errorlevel 1 exit /b 1

echo [0/4] 正在创建本地运行环境...
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
  echo 备份目录 .venv.stale 已经存在。
  echo 请先重命名或删除它，然后重新双击启动脚本。
  pause
  exit /b 1
)
ren ".venv" ".venv.stale" >nul 2>nul
if errorlevel 1 (
  echo 备份旧运行环境失败，请关闭可能正在占用它的窗口后重试。
  pause
  exit /b 1
)
echo 已把旧运行环境备份到 .venv.stale。
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
echo 8765 到 8785 之间没有可用端口，请关闭其他本地服务后再试。
pause
exit /b 1

:check_port
netstat -ano | findstr ":%1" >nul
if %errorlevel%==0 exit /b 1
exit /b 0

:fail
echo 启动失败。请查看上面的错误信息，或打开 docs\TROUBLESHOOTING.md 查看排错说明。
pause
exit /b 1
