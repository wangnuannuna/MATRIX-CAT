@echo off
chcp 65001 >nul
title Matrix_Cat launcher
rem Double-click to start the web console (http://127.0.0.1:8799).
rem This only boots run.py; run.py itself picks a python that has cloakbrowser
rem (auto-switches to conda313 if needed).
where python >nul 2>nul && goto usepython
where py >nul 2>nul && goto usepy
echo [ERROR] Python not found on PATH.
echo Run it manually in your conda313 shell:
echo    python "%~dp0run.py"
goto theend
:usepython
python "%~dp0run.py" %*
goto theend
:usepy
py "%~dp0run.py" %*
:theend
echo.
pause
