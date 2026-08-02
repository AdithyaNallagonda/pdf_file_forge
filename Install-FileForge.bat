@echo off
REM Double-click to set up the run-from-source version (venv + Start Menu shortcut).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
