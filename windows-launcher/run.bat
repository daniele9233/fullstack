@echo off
REM Avvia Ansible Launcher. Al primo avvio prepara tutto da solo.
setlocal
cd /d "%~dp0"

REM Su Windows Python risponde a "py" (il launcher ufficiale) oppure a
REM "python". Se si prova solo il secondo, su molte installazioni il comando
REM apre il Microsoft Store invece di eseguire Python.
set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY python --version >nul 2>&1 && set PY=python
if not defined PY goto :nopython

if not exist ".venv\Scripts\python.exe" (
    echo Primo avvio: preparo l'ambiente Python...
    %PY% -m venv .venv || goto :errore
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip --quiet
    echo Installo le dipendenze, ci vuole un minuto...
    python -m pip install -r requirements.txt || goto :errore
) else (
    call ".venv\Scripts\activate.bat"
)

python main.py
goto :fine

:nopython
echo.
echo Python non e' installato, oppure non e' nel PATH.
echo.
echo   1. Scarica Python da https://www.python.org/downloads/
echo   2. Durante l'installazione SPUNTA "Add Python to PATH"
echo   3. Chiudi e riapri questa finestra, poi riprova
echo.
pause
goto :fine

:errore
echo.
echo Preparazione fallita. Copia il messaggio qui sopra.
pause

:fine
endlocal
