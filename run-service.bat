@echo off
REM Keep scraping every 15 minutes (on the quarter hour, WIB) until this window is closed.
REM Double-click to start, or point Windows Task Scheduler at this file with
REM "Run whether user is logged on or not" + "Restart if the task fails".
cd /d "%~dp0"
title BCA Forex Scraper
python -m bca_forex run
echo.
echo Scraper stopped. Press any key to close.
pause >nul
