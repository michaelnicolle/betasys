@echo off
REM Starts the invoice app and opens it in your browser.
cd /d "%~dp0"

REM A .venv synced from another machine points at a Python that isn't installed
REM here, so check it actually runs rather than just that it exists.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import flask, reportlab" >nul 2>&1
    if errorlevel 1 (
        echo Rebuilding the Python environment for this machine...
        rmdir /s /q ".venv" >nul 2>&1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo Setting up for first use, this takes a minute...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 (
        echo.
        echo Could not find Python. Install it from https://www.python.org/downloads/
        echo and tick "Add python.exe to PATH" during setup, then run this file again.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo.
        echo Could not download Flask and ReportLab. Check your internet
        echo connection and run this file again.
        pause
        exit /b 1
    )
)

echo.
echo Invoicer is running at http://127.0.0.1:5000
echo Close this window to stop it.
echo.
".venv\Scripts\python.exe" app.py
pause
