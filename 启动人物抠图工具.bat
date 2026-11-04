@echo off
setlocal
chcp 65001 >nul
title Person Cutout - Environment Check
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1" %*
set "CUTOUT_EXIT=%ERRORLEVEL%"
if not "%CUTOUT_EXIT%"=="0" (
    echo.
    echo Setup or startup failed. See the log path printed above.
    pause
)
exit /b %CUTOUT_EXIT%
