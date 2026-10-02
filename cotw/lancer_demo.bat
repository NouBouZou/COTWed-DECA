@echo off
rem Cree si besoin le projet de demonstration (faux COTW, sans le jeu) puis l'ouvre dans DECA.
rem Le projet est dans ..\work_demo : le vrai projet (..\work\hp) n'est jamais touche.
cd /d %~dp0..
call venv\Scripts\activate.bat
for /f "delims=" %%p in ('python cotw\demo_projet.py') do set DEMO_PROJECT=%%p
if not defined DEMO_PROJECT (
  echo Echec de la creation du projet demo.
  pause
  exit /b 1
)
python python\deca_gui\deca_gui\entry_point.py "%DEMO_PROJECT%"
pause
