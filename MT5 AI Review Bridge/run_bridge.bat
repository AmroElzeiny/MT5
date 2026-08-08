@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
start "AI Review Dashboard" http://127.0.0.1:1234/
python -m bridge
