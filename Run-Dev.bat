@echo off
REM ---------------------------------------------------------------------------
REM Run FileForge straight from source - NO build, NO install, NO exe.
REM
REM Use this while testing code changes. Edit a .py file, close the window,
REM double-click this again: the change is live. Building the exe is only
REM needed when shipping a copy to someone else.
REM
REM Uses .buildenv (created by build.ps1 / install.ps1) because it already has
REM every runtime dependency. Falls back to whatever python is on PATH.
REM
REM Pass-through args work too, e.g.:
REM     Run-Dev.bat --classic-ui
REM     Run-Dev.bat "C:\case\claim.pdf" --op pdf_to_md
REM ---------------------------------------------------------------------------
cd /d "%~dp0"

set "PY=%~dp0.buildenv\Scripts\python.exe"
if exist "%PY%" goto :run

set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" goto :run

echo No project virtual environment found (.buildenv or .venv).
echo Falling back to the system Python.
echo If imports fail, run:  powershell -ExecutionPolicy Bypass -File install.ps1
echo.
set "PY=python"

:run
echo Running FileForge from source using: %PY%
echo Workspace: D:\FileForge  (same settings/results as the installed app)
echo.
"%PY%" "%~dp0main.py" %*

if errorlevel 1 (
    echo.
    echo FileForge exited with an error - see the messages above,
    echo and D:\FileForge\logs\fileforge.log for the full traceback.
    pause
)
