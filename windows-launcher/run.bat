@echo off
REM Avvia il software senza compilarlo. Serve Python 3.10 o piu' recente.
cd /d "%~dp0"

if not exist ".venv\" (
    echo Primo avvio: creo l'ambiente Python e installo le dipendenze...
    python -m venv .venv || goto :errore
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt || goto :errore
) else (
    call .venv\Scripts\activate.bat
)

python main.py
goto :fine

:errore
echo.
echo Qualcosa e' andato storto. Verifica che Python sia installato:
echo     python --version
pause

:fine
