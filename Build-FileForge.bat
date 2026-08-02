@echo off
REM Double-click this instead of typing PowerShell commands.
REM It always runs build.ps1 from the folder it lives in.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
echo.
echo Build script finished. Review the messages above.
pause
