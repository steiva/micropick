@echo off
rem Slice picking, double-clicked: no terminal needed.
call "%USERPROFILE%\miniconda3\condabin\conda.bat" run --no-capture-output -n lab python -m micropick.slices %*
if errorlevel 1 pause
