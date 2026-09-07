@echo off
cd /d "%~dp0\..\.."
python -m rnd_factory investigate --last-trades 100 --mode no-ai
