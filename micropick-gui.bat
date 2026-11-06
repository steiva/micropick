@echo off
rem micropick from this checkout, double-clicked: no terminal needed.
call "%USERPROFILE%\miniconda3\condabin\conda.bat" run --no-capture-output -n lab python -m micropick.gui %*
if errorlevel 1 pause
