@echo off
cd /d "%~dp0"
set /p PID=PID:
python main.py %PID%
