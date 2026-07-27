@echo off
REM Scrape once now, then show what was stored.
cd /d "%~dp0"
python -m bca_forex once
echo.
python -m bca_forex latest
echo.
pause
