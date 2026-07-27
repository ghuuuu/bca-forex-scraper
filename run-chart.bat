@echo off
REM Build chart.html from the stored history and open it in the browser.
cd /d "%~dp0"
python -m bca_forex chart --out chart.html --open
echo.
pause
