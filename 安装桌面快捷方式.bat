@echo off
rem ============================================================
rem  Create a desktop shortcut with the app icon.
rem  Optional, but it makes the program much easier to start.
rem ============================================================
setlocal
cd /d "%~dp0"

set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" goto :missing

set PYTHONIOENCODING=utf-8
"%PY%" "%~dp0make_shortcut.py"
echo.
pause
exit /b 0

:missing
echo.
echo   [ERROR] Bundled runtime not found:
echo           runtime\python\python.exe
echo.
pause
exit /b 1
