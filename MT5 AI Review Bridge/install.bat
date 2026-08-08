@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === Local AI Review Bridge : install ===
echo.

where py >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python launcher ^(py^) not found. Install Python 3.11+ from python.org first.
    goto :fail
)

rem --- Pick a supported interpreter -------------------------------------------
rem "py -3" may resolve to a free-threaded build (e.g. 3.13t). Several required
rem wheels (rpds-py / pydantic-core) have no free-threaded builds and their Rust
rem backends refuse to compile against free-threaded CPython below 3.14, so we
rem explicitly select a normal GIL build instead.
set "PYTAG="
for %%V in (3.12 3.13 3.11 3.14) do (
    if not defined PYTAG (
        py -%%V -c "import sysconfig,sys; sys.exit(1 if sysconfig.get_config_var('Py_GIL_DISABLED') else 0)" >nul 2>nul
        if !errorlevel! equ 0 set "PYTAG=%%V"
    )
)
if not defined PYTAG (
    echo [ERROR] No supported Python found.
    echo         Need a standard ^(non free-threaded^) CPython 3.11 - 3.14.
    echo         Installed versions:
    py -0p
    goto :fail
)
for /f "delims=" %%P in ('py -%PYTAG% -c "import sys; print(sys.executable)"') do set "PYEXE=%%P"
echo [1/5] Using Python %PYTAG%: %PYEXE%

rem --- Discard an unusable existing venv ---------------------------------------
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sysconfig,sys; sys.exit(1 if sysconfig.get_config_var('Py_GIL_DISABLED') else 0)" >nul 2>nul
    if errorlevel 1 (
        echo       Existing .venv uses an unsupported free-threaded Python. Recreating it.
        rmdir /s /q ".venv"
    )
) else (
    if exist ".venv" (
        echo       Existing .venv is incomplete. Recreating it.
        rmdir /s /q ".venv"
    )
)

rem --- Create the venv ----------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [2/5] Creating virtual environment in .venv
    py -%PYTAG% -m venv ".venv"
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        goto :fail
    )
) else (
    echo [2/5] Reusing existing virtual environment in .venv
)

set "VPY=%CD%\.venv\Scripts\python.exe"
if not exist "%VPY%" (
    echo [ERROR] .venv\Scripts\python.exe is missing after venv creation.
    goto :fail
)

rem --- Dependencies -------------------------------------------------------------
echo [3/5] Upgrading pip
"%VPY%" -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] pip upgrade failed. Check your network or proxy settings.
    goto :fail
)

echo [4/5] Installing requirements
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency installation failed. See the pip output above.
    goto :fail
)

rem --- Verify -------------------------------------------------------------------
echo [5/5] Verifying installation
"%VPY%" -c "import fastapi, uvicorn, jsonschema, httpx, dotenv, multipart"
if errorlevel 1 (
    echo [ERROR] One or more dependencies did not import correctly.
    goto :fail
)
"%VPY%" -c "import bridge.app; from bridge.config import settings; print('Config OK. Bridge URL:', settings.base_url)"
if errorlevel 1 (
    echo [ERROR] The bridge package failed to import.
    goto :fail
)

echo.
echo Installation complete.
echo Next: run register_chat.bat once, then run_bridge.bat.
endlocal
exit /b 0

:fail
echo.
echo Installation FAILED. Nothing above this line succeeded past the first error.
endlocal
exit /b 1
