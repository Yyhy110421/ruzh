@echo off
rem One-time setup. Finds or installs a Python interpreter, then creates the
rem virtual environment, installs dependencies and deploys the VC++ runtime DLLs.
rem
rem   install.bat            normal setup
rem   install.bat -Models    also download the translation models
rem   install.bat -DryRun    only report which Python would be used
setlocal
cd /d "%~dp0"
rem PowerShell and the Python helpers both write UTF-8 to the console.
chcp 65001 >nul 2>nul

where powershell >nul 2>nul
if errorlevel 1 goto :nops

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\bootstrap.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo   Setup finished. Double-click 启动.bat to run the app.
) else (
  echo   Setup failed with code %RC%.
)
pause
exit /b %RC%

:nops
echo   [WARN] powershell not found, falling back to a direct call.
set "PYEXE="
where py >nul 2>nul && set "PYEXE=py"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE goto :nopython
%PYEXE% "scripts\setup_env.py" %*
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%

:nopython
echo.
echo   [ERROR] No Python found and powershell is unavailable.
echo   Install Python 3.10+ from https://www.python.org/downloads/windows/
echo   and tick "Add python.exe to PATH" and "tcl/tk and IDLE".
echo.
pause
exit /b 1
