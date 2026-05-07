@echo off
setlocal EnableExtensions
chcp 65001 >nul

cd /d "%~dp0"

echo.
echo ========================================
echo   resume-bot 本地求职助手
echo ========================================
echo.
echo 这个窗口会启动本地网页。
echo 第一次运行会安装环境，可能需要几分钟。
echo 请不要在安装过程中直接关闭这个窗口。
echo.
echo 如果浏览器没有自动打开，请看下面显示的 Local URL。
echo.

call "%~dp0start_resume_bot.cmd" %*

echo.
echo 如果上面提示 Startup failed，请先打开 docs\TROUBLESHOOTING.md 查看排错说明。
echo.
pause
