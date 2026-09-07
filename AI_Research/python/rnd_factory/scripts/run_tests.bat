@echo off
cd /d "%~dp0\..\.."
python -m unittest discover -s rnd_factory\tests -v
