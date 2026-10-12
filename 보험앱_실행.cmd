@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0START_APP.ps1"
set "APP_EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%APP_EXIT_CODE%"=="0" (
  echo App startup failed. Keep this window open and share the error shown above.
) else (
  echo App stopped normally.
)
pause
exit /b %APP_EXIT_CODE%
