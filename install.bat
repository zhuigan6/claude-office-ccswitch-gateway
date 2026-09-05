@echo off
REM Claude Office Gateway - one-click install (double-click friendly).
REM If the release bundle includes the _python runtime, no Python installation is needed.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
