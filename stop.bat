@echo off
rem ==========================================================================
rem  EzVTT - stop the server
rem
rem  Double-click this file, or run it from a terminal. Shuts the server down
rem  cleanly so in-flight requests finish and the database closes properly.
rem
rem      stop.bat                      wait up to 15 seconds
rem      stop.bat -TimeoutSeconds 30   wait longer
rem
rem  If the server will not stop, use scripts\kill.ps1 to force it. That does
rem  not give the database a chance to close, so try this first.
rem ==========================================================================

setlocal

cd /d "%~dp0"

rem Double-clicked from Explorer? Then hold the window open at the end, or the
rem result vanishes before it can be read. Set EZVTT_NO_PAUSE=1 to suppress it:
rem "cmd /c stop.bat" from a script looks identical to a double-click from here,
rem and a script that blocks on a keypress nobody is there to press just hangs.
set "EZVTT_PAUSE="
if not defined EZVTT_NO_PAUSE (
    echo %cmdcmdline% | find /i "%~nx0" >nul
    if not errorlevel 1 set "EZVTT_PAUSE=1"
)

if not exist "scripts\stop.ps1" (
    echo.
    echo   Could not find scripts\stop.ps1
    echo   This file needs to stay in the top level of the EzVTT folder.
    echo.
    if defined EZVTT_PAUSE pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\stop.ps1" %*
set "EZVTT_EXIT=%ERRORLEVEL%"

if not "%EZVTT_EXIT%"=="0" (
    echo.
    echo   Force it with:  powershell -File scripts\kill.ps1
)

rem Always pause when double-clicked: unlike start, this finishes immediately,
rem so without it the window flashes past and says nothing.
if defined EZVTT_PAUSE (
    echo.
    pause
)

exit /b %EZVTT_EXIT%
