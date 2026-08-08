@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Run install.bat first.
    goto :fail
)

set "VPY=%CD%\.venv\Scripts\python.exe"

"%VPY%" -c "import fastapi, uvicorn, jsonschema, httpx, dotenv" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Dependencies are missing or incomplete. Run install.bat first.
    goto :fail
)

call ".venv\Scripts\activate.bat"
"%VPY%" -m bridge --register
if errorlevel 1 (
    echo.
    echo [ERROR] Registration did not complete. See the message above.
    goto :fail
)

echo.
echo Registration complete. Next: run run_bridge.bat.
endlocal
exit /b 0

:fail
endlocal
exit /b 1
