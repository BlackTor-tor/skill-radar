@echo off
chcp 65001 >nul
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
python "%~dp0src\skill_monitor.py" %*
pause
