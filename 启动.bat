@echo off
rem Launch without a console window. Works in both layouts:
rem   release package  -> runtime\python\pythonw.exe  (bundled interpreter)
rem   source checkout  -> .venv\Scripts\pythonw.exe   (made by install.bat)
setlocal
cd /d "%~dp0"

if exist "runtime\python\pythonw.exe" (
    start "" "runtime\python\pythonw.exe" "main.py"
    exit /b 0
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "main.py"
    exit /b 0
)

echo.
echo   [ERROR] No Python runtime found.
echo.
echo   Release package: runtime\python\pythonw.exe is missing -
echo   re-extract the package, or read the manual next to this file.
echo.
echo   Source checkout: run install.bat once, then start again.
echo.
pause
exit /b 1
