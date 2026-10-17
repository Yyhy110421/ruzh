@echo off
rem Same as the normal launcher but keeps a console open so errors are visible.
setlocal
cd /d "%~dp0"

if exist "runtime\python\python.exe" (
    "runtime\python\python.exe" "main.py"
) else if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "main.py"
) else (
    echo.
    echo   [ERROR] No Python found. A release package should have
    echo   runtime\python\python.exe; a source checkout needs install.bat
    echo   run once first.
    echo.
    pause
    exit /b 1
)
echo.
echo   Application exited with code %ERRORLEVEL%.
pause
exit /b %ERRORLEVEL%
