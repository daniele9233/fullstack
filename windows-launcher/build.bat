@echo off
REM Costruisce dist\AnsibleLauncher.exe: un file solo, da copiare dove serve.
REM Sulla macchina di destinazione non servira' Python.
setlocal
cd /d "%~dp0"

set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY python --version >nul 2>&1 && set PY=python
if not defined PY goto :nopython

if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv || goto :errore
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt pyinstaller || goto :errore

REM --onefile:  un eseguibile unico, senza cartelle di contorno
REM --windowed: niente finestra nera del prompt dietro all'applicazione
pyinstaller --noconfirm --clean --onefile --windowed ^
    --name AnsibleLauncher ^
    --hidden-import paramiko ^
    main.py || goto :errore

echo.
echo ============================================
echo   Pronto:  dist\AnsibleLauncher.exe
echo ============================================
echo.
pause
goto :fine

:nopython
echo Python non e' installato o non e' nel PATH. Vedi il README.
pause
goto :fine

:errore
echo.
echo Compilazione fallita.
pause

:fine
endlocal
