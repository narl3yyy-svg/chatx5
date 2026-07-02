@echo off
REM Remove chatx5 install (.venv), pip package, and all app data on Windows.
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo chatx5 Windows Uninstall
echo ========================
echo.

echo [1/6] Stopping chatx5 server and releasing ports...
call "%~dp0scripts\stop-chatx5.bat"
echo   Done.

echo [2/6] Removing Python environment and package...
if exist ".venv" (
  rmdir /s /q ".venv"
  echo   Removed .venv
) else (
  echo   No .venv found
)
if exist "chatx5.egg-info" (
  rmdir /s /q "chatx5.egg-info"
  echo   Removed chatx5.egg-info
)
if exist "build" (
  rmdir /s /q "build"
  echo   Removed build/
)
if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe -m pip uninstall -y chatx5 2>nul
) else if exist ".venv\Scripts\pip.exe" (
  .venv\Scripts\pip.exe uninstall -y chatx5 2>nul
)
where python >nul 2>&1 && python -m pip uninstall -y chatx5 2>nul

echo [3/6] Application data (identity, settings, chat history, transfers)...
set "CONFIG_DIR=%USERPROFILE%\.config\chatx5"
set "DATA_DIR=%USERPROFILE%\.local\share\chatx5"
set "CACHE_DIR=%LOCALAPPDATA%\chatx5"
if not defined CACHE_DIR set "CACHE_DIR=%USERPROFILE%\.cache\chatx5"
if defined CHATX5_PORTABLE set "PORTABLE_DIR=%CHATX5_PORTABLE%\chatx5-data"
if not defined PORTABLE_DIR if exist "chatx5-data" set "PORTABLE_DIR=%CD%\chatx5-data"

if exist "%CONFIG_DIR%" (
  echo   Config: %CONFIG_DIR%
  set /p RM1=   Remove config? [y/N]:
  if /I "!RM1!"=="y" rmdir /s /q "%CONFIG_DIR%" && echo   Removed config.
)
if exist "%DATA_DIR%" (
  echo   Data: %DATA_DIR%
  set /p RM2=   Remove data? [y/N]:
  if /I "!RM2!"=="y" rmdir /s /q "%DATA_DIR%" && echo   Removed data.
)
if exist "%CACHE_DIR%" (
  echo   Cache: %CACHE_DIR%
  set /p RM3=   Remove cache? [y/N]:
  if /I "!RM3!"=="y" rmdir /s /q "%CACHE_DIR%" && echo   Removed cache.
)
if defined PORTABLE_DIR if exist "!PORTABLE_DIR!" (
  echo   Portable: !PORTABLE_DIR!
  set /p RM4=   Remove portable data? [y/N]:
  if /I "!RM4!"=="y" rmdir /s /q "!PORTABLE_DIR!" && echo   Removed portable data.
)

echo [4/6] Clearing RNS temp sockets (if any)...
if exist "%TEMP%\rns" (
  rmdir /s /q "%TEMP%\rns" 2>nul
  echo   Cleared %TEMP%\rns
) else (
  echo   No RNS temp folder found
)

echo [5/6] Checking for leftover chatx5 commands...
where chatx5 >nul 2>&1 && echo   WARNING: chatx5 still on PATH

echo [6/6] Cleanup complete.
echo.
echo To run again:  run.bat web --share
echo.
endlocal
exit /b 0