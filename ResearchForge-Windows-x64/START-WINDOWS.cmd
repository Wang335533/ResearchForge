@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if /i "%PROCESSOR_ARCHITECTURE%"=="x86" if not defined PROCESSOR_ARCHITEW6432 (
  echo 此包适用于 64 位 Windows 电脑，请使用与电脑匹配的版本。
  pause
  exit /b 1
)
"%~dp0runtime\python\python.exe" -I -B -X utf8 "%~dp0portable.py" %*
set "rf_exit=%errorlevel%"
if not "%rf_exit%"=="0" (
  echo.
  echo 启动失败，请保留上面的信息。
  pause
)
exit /b %rf_exit%
