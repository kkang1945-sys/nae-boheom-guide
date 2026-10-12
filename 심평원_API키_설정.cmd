@echo off
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0SET_HIRA_API_KEY_GUI.ps1"
echo.
if errorlevel 1 (
  echo Setup failed. Check the error message above.
) else (
  echo Setup window closed.
)
pause
