@echo off
rem The broker's entry point on cmd.exe and PowerShell.
rem
rem See the sh sibling in this directory for why the bare command matters and
rem why interpreter resolution probes by execution rather than by presence.
rem
rem `py -3` is tried before `python` because on Windows `python` is frequently
rem an app execution alias that resolves, does nothing, and would otherwise look
rem like a working interpreter.

setlocal EnableExtensions

set "ENTRY=%~dp0..\scripts\kyrio\__main__.py"
set "RECORDED=%USERPROFILE%\.claude\kyrio\state\interpreter"
set "PYCMD="

if defined KYRIO_PYTHON call :try "%KYRIO_PYTHON%"
if not defined PYCMD if exist "%RECORDED%" (
    for /f "usebackq delims=" %%p in ("%RECORDED%") do (
        if not defined PYCMD if not "%%p"=="" call :try "%%p"
    )
)
if not defined PYCMD call :try py -3
if not defined PYCMD call :try python3
if not defined PYCMD call :try python
if not defined PYCMD goto :no_interpreter
if not exist "%ENTRY%" goto :no_entry

%PYCMD% "%ENTRY%" %*
exit /b %ERRORLEVEL%

:try
rem Probe by running it. Sets PYCMD on success, leaves it unset otherwise.
setlocal
%* -c "import sys; sys.exit(sys.version_info[:2] < (3, 12))" >nul 2>&1
if errorlevel 1 (endlocal & exit /b 1)
endlocal & set "PYCMD=%*" & exit /b 0

:no_interpreter
echo {"status":"error","message":"no Python 3.12 or newer found"}
echo ---
echo kyrio needs Python 3.12 or newer, and none was found.
echo.
echo Tried, in order: KYRIO_PYTHON, the interpreter recorded by
echo /kyrio:setup, py -3, python3, python.
echo.
echo Install Python 3.12 or newer, then run /kyrio:setup. If it is already
echo installed somewhere off PATH, set KYRIO_PYTHON to its absolute path.
exit /b 1

:no_entry
echo {"status":"error","message":"broker entry point is missing"}
echo ---
echo Expected to find it at: %ENTRY%
echo.
echo The plugin install looks incomplete. Reinstall it, then run
echo /kyrio:setup.
exit /b 1
