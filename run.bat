@echo off
REM SQL Agent Query Runner startup script for Windows

REM Activate virtual environment if it exists
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

REM Check if .env file exists
if not exist .env (
    echo Error: .env file not found!
    echo Please copy .env.example to .env and configure your settings.
    pause
    exit /b 1
)

REM Start the Flask application
echo Starting SQL Agent Query Runner...
python app.py
pause
