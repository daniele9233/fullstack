@echo off
REM Costruisce AnsibleLauncher.exe, un singolo file da copiare dove serve.
cd /d "%~dp0"

if not exist ".venv\" (
    python -m venv .venv || goto :errore
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller || goto :errore

REM --windowed: niente finestra nera del prompt dietro all'applicazione.
REM --onefile:  un eseguibile solo, senza cartelle di contorno.
pyinstaller --noconfirm --clean --onefile --windowed ^
    --name AnsibleLauncher ^
    --hidden-import paramiko ^
    main.py || goto :errore

echo.
echo Fatto: dist\AnsibleLauncher.exe
goto :fine

:errore
echo.
echo Compilazione fallita.
pause

:fine
