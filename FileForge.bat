@echo off
rem Console launcher (shows errors). Start Menu shortcut uses pythonw instead.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (".venv\Scripts\python.exe" main.py) else (python main.py)
if errorlevel 1 pause
