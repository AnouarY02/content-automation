@echo off
:: ============================================================
:: AY Marketing OS — Volledig Autonoom Systeem
:: Start de backend (API) + scheduler (content productie) tegelijk.
::
:: WAT DIT DOET:
::   Venster 1 — FastAPI backend (http://localhost:8000)
::   Venster 2 — Scheduler: produceert automatisch content
::               en publiceert op TikTok om 07:00 en 19:00
::
:: GEBRUIK:
::   Dubbelklik dit bestand, of run in terminal:
::   deployment\start_autonomous.bat
:: ============================================================

title AY Marketing OS — Autonoom

echo.
echo  ============================================================
echo   AY Marketing OS — Autonoom Systeem
echo  ============================================================
echo.

:: Ga naar de content-automation map (relatief vanaf deployment\)
cd /d "%~dp0.."

:: Controleer of .env bestaat
if not exist ".env" (
    echo [FOUT] .env bestand niet gevonden in %cd%
    echo Kopieer .env.example naar .env en vul je API keys in.
    echo.
    pause
    exit /b 1
)

:: Activeer Python virtual environment als aanwezig
if exist "..\\.venv\\Scripts\\activate.bat" (
    echo [Setup] Virtual environment activeren...
    call "..\\.venv\\Scripts\\activate.bat"
) else if exist ".venv\\Scripts\\activate.bat" (
    echo [Setup] Virtual environment activeren...
    call ".venv\\Scripts\\activate.bat"
) else (
    echo [!] Geen .venv gevonden — gebruik systeem Python
)

:: Controleer of Python beschikbaar is
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [FOUT] Python niet gevonden. Installeer Python 3.12+
    pause
    exit /b 1
)

echo.
echo [1/2] FastAPI backend starten (venster 1)...
start "AY Backend — http://localhost:8000" cmd /k "cd /d "%~dp0.." && python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload"

:: Wacht 3 seconden zodat de backend kan opstarten
timeout /t 3 /nobreak >nul

echo [2/2] Scheduler starten — content productie 07:00 + 19:00 (venster 2)...
start "AY Scheduler — Content Productie" cmd /k "cd /d "%~dp0.." && python workflows/scheduler.py"

echo.
echo  ============================================================
echo   Systeem draait!
echo.
echo   Backend API:  http://localhost:8000
echo   API Docs:     http://localhost:8000/docs
echo   Goedkeuring:  http://localhost:8000/docs#/Approvals
echo.
echo   Content wordt automatisch geproduceerd om:
echo     07:00 (morning slot)
echo     19:00 (evening slot)
echo.
echo   Sluit de twee vensters om te stoppen.
echo  ============================================================
echo.

:: Open browser naar API docs
timeout /t 2 /nobreak >nul
start "" "http://localhost:8000/docs"

pause
