@echo off
:: ============================================================
:: AY Marketing OS — Start Script
:: Dubbelklik dit bestand om het systeem op te starten.
:: ============================================================

title AY Marketing OS

echo.
echo  ============================================================
echo   AY Marketing Operating System v1.0
echo  ============================================================
echo.

:: Controleer of .env bestaat
if not exist "C:\AY-automatisering\.env" (
    echo [FOUT] .env bestand niet gevonden!
    echo Kopieer .env.example naar .env en vul je API keys in.
    echo.
    pause
    exit /b 1
)

:: Activeer Python virtual environment als die bestaat
if exist "C:\AY-automatisering\.venv\Scripts\activate.bat" (
    echo [1/3] Virtual environment activeren...
    call "C:\AY-automatisering\.venv\Scripts\activate.bat"
) else (
    echo [!] Geen .venv gevonden, gebruik systeem Python
)

:: Start Redis als Docker beschikbaar is (optioneel)
where docker >nul 2>&1
if %errorlevel% == 0 (
    echo [2/3] Redis starten via Docker...
    docker run -d --name ay-redis -p 6379:6379 redis:alpine 2>nul
    echo     Redis draait op localhost:6379
) else (
    echo [2/3] Docker niet gevonden, Redis wordt overgeslagen
)

:: Start de FastAPI backend
echo [3/3] Backend starten op http://localhost:8000
echo.
echo  API docs beschikbaar op: http://localhost:8000/docs
echo  Druk Ctrl+C om te stoppen
echo.

cd /d "C:\AY-automatisering"
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

pause
