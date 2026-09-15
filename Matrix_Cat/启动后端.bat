@echo off
rem Matrix_Cat backend launcher.
rem ASCII-only on purpose: cmd.exe reads .bat in the GBK codepage, so Chinese
rem here (or a hardcoded Chinese python path) gets garbled. All the real logic
rem lives in run_backend.py -- Python handles the Chinese install paths fine.
where python >nul 2>nul && ( python "%~dp0run_backend.py" %* & pause & exit /b )
where py     >nul 2>nul && ( py     "%~dp0run_backend.py" %* & pause & exit /b )
echo Python not found on PATH.
echo Run it in your conda313 shell:   python run_backend.py
pause
