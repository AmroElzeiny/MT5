@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher 'py' was not found. Install Python 3.11+ and retry.
  exit /b 1
)

if exist .venv\Scripts\python.exe (
  rem greenlet and rpds-py ship no free-threaded wheels and fail to compile there.
  .venv\Scripts\python.exe -c "import sysconfig,sys; sys.exit(1 if sysconfig.get_config_var('Py_GIL_DISABLED') else 0)" >nul 2>nul
  if errorlevel 1 (
    echo Existing .venv uses a free-threaded ^(GIL-disabled^) Python build.
    echo Dependencies cannot be installed there. Delete .venv and re-run install.bat.
    exit /b 1
  )
) else (
  set "PYTAG="
  for %%v in (3.12 3.11 3.13 3) do (
    if not defined PYTAG (
      py -%%v -c "import sysconfig,sys; sys.exit(1 if sysconfig.get_config_var('Py_GIL_DISABLED') else 0)" >nul 2>nul
      if not errorlevel 1 set "PYTAG=%%v"
    )
  )
  if not defined PYTAG (
    echo No GIL-enabled Python 3.11+ found. Install Python 3.12 and retry.
    exit /b 1
  )
  echo Creating .venv with Python !PYTAG! ...
  py -!PYTAG! -m venv .venv
  if errorlevel 1 (
    echo Failed to create .venv.
    exit /b 1
  )
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 (
  echo pip upgrade failed.
  exit /b 1
)
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency install failed.
  exit /b 1
)
python -c "from bridge.config import settings; print('Bridge config initialized:', settings.base_url)"
if errorlevel 1 (
  echo Bridge config import failed.
  exit /b 1
)
echo.
echo Installed. No bundled Chromium is downloaded; runtime uses installed Chrome/Edge.
echo Next: register_chat.bat, configure selectors in .env, then diagnose_selectors.bat.
endlocal
exit /b 0
