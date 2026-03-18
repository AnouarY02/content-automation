@echo off
:: ============================================================
:: AY Marketing OS — Eerste Installatie
:: Voer dit eenmalig uit na het klonen/kopiëren van het systeem.
:: ============================================================

title AY Marketing OS — Setup

echo.
echo  ============================================================
echo   AY Marketing OS — Eerste installatie
echo  ============================================================
echo.

cd /d "C:\AY-automatisering"

:: Controleer Python
echo [1/5] Python versie controleren...
python --version
if %errorlevel% neq 0 (
    echo [FOUT] Python niet gevonden. Installeer Python 3.12+ van https://python.org
    pause
    exit /b 1
)

:: Maak virtual environment
echo.
echo [2/5] Virtual environment aanmaken...
python -m venv .venv
call .venv\Scripts\activate.bat

:: Installeer dependencies
echo.
echo [3/5] Dependencies installeren...
pip install --upgrade pip
pip install -r requirements.txt

:: Kopieer .env als die niet bestaat
echo.
echo [4/5] .env configureren...
if not exist ".env" (
    copy ".env.example" ".env"
    echo     .env aangemaakt — VERGEET NIET je API keys in te vullen!
) else (
    echo     .env bestaat al, wordt overgeslagen
)

:: Controleer FFmpeg
echo.
echo [5/5] FFmpeg controleren...
where ffmpeg >nul 2>&1
if %errorlevel% == 0 (
    echo     FFmpeg gevonden
) else (
    echo [!] FFmpeg niet gevonden in PATH
    echo     Download van https://ffmpeg.org/download.html
    echo     Voeg de bin/ map toe aan je Windows PATH
)

echo.
echo  ============================================================
echo   Installatie klaar!
echo.
echo   Volgende stappen:
echo   1. Open C:\AY-automatisering\.env en vul je API keys in
echo   2. Vul C:\AY-automatisering\configs\app_registry.json in met je app-data
echo   3. Dubbelklik deployment\start_system.bat om te starten
echo  ============================================================
echo.
pause
