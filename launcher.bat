@echo off
REM ============================================================
REM  Qalam Launcher
REM  Startet die App mit dem projekteigenen venv (Python 3.12).
REM  Doppelklick genuegt. Zum Beenden: Icon im System-Tray
REM  (unten rechts) -> Rechtsklick -> Exit, oder dieses Fenster
REM  schliessen.
REM ============================================================

cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [FEHLER] venv nicht gefunden unter "%~dp0venv".
    echo Bitte Setup erneut ausfuehren.
    pause
    exit /b 1
)

echo Starte Qalam... ^(Aktivierungs-Hotkey: Strg+Shift+Leertaste^)
"venv\Scripts\python.exe" run.py

REM Falls die App unerwartet beendet wird, Fenster offen halten,
REM damit eventuelle Fehlermeldungen lesbar bleiben.
if errorlevel 1 (
    echo.
    echo Qalam wurde mit einem Fehler beendet.
    pause
)
