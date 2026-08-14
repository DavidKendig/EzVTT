@echo off
rem ==========================================================================
rem  EzVTT - start the server
rem
rem  Double-click this file, or run it from a terminal. Any arguments are
rem  passed straight through to scripts\start.ps1:
rem
rem      start.bat                     this machine only (default)
rem      start.bat -Mode lan           players join over your network
rem      start.bat -Mode lan -Port 9000
rem      start.bat -NoBypass           disable the beta login bypass
rem
rem  See docs\RUN_MODES.md for what each mode does.
rem ==========================================================================

setlocal

rem Double-clicking from Explorer does not necessarily set the working
rem directory to this folder, and "Run as administrator" sets it to system32.
rem Anchor to wherever this file actually lives.
cd /d "%~dp0"

rem Was this double-clicked rather than run from an existing console? If so the
rem window closes the instant we finish, taking any error message with it.
rem Set EZVTT_NO_PAUSE=1 to suppress it: "cmd /c start.bat" from a script looks
rem identical to a double-click from here, and a script that blocks on a
rem keypress nobody is there to press just hangs.
set "EZVTT_PAUSE="
if not defined EZVTT_NO_PAUSE (
    echo %cmdcmdline% | find /i "%~nx0" >nul
    if not errorlevel 1 set "EZVTT_PAUSE=1"
)

if not exist "scripts\start.ps1" (
    echo.
    echo   Could not find scripts\start.ps1
    echo   This file needs to stay in the top level of the EzVTT folder.
    echo.
    if defined EZVTT_PAUSE pause
    exit /b 1
)

rem -ExecutionPolicy Bypass because the default policy on a fresh Windows
rem install refuses unsigned local scripts, which would otherwise make this
rem fail with an unhelpful error on someone else's machine.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\start.ps1" %*
set "EZVTT_EXIT=%ERRORLEVEL%"

if not "%EZVTT_EXIT%"=="0" (
    echo.
    echo   EzVTT exited with code %EZVTT_EXIT%.
    if defined EZVTT_PAUSE pause
)

exit /b %EZVTT_EXIT%
