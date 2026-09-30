@echo off
rem Lance DECA depuis les sources. A placer dans C:\dev\deca\cotw\
cd /d %~dp0..
call venv\Scripts\activate.bat
python python\deca_gui\deca_gui\entry_point.py
pause
