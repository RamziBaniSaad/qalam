#!/bin/bash
# ============================================================
#  Qalam Launcher (macOS)
#  Startet die App mit dem projekteigenen venv (Python 3.12).
#  Doppelklick im Finder genuegt. Zum Beenden: Icon in der
#  Menueleiste -> Exit, oder dieses Terminal-Fenster schliessen.
#
#  Beim ersten Start fragt macOS nach Mikrofon- und
#  Bedienungshilfen-Rechten -> siehe MACOS.md.
# ============================================================
cd "$(dirname "$0")" || exit 1

PY="venv/bin/python3"
if [ ! -x "$PY" ]; then
    PY="venv/bin/python"
fi

if [ ! -x "$PY" ]; then
    echo "[FEHLER] venv nicht gefunden unter $(pwd)/venv."
    echo "Bitte zuerst das Setup ausfuehren (siehe MACOS.md):"
    echo "  python3 -m venv venv && source venv/bin/activate && pip install ."
    read -r -p "Enter zum Schliessen..."
    exit 1
fi

echo "Starte Qalam... (Aktivierungs-Hotkey: siehe config.yaml)"
"$PY" run.py

# Bei Fehler das Fenster offen halten, damit Meldungen lesbar bleiben.
if [ $? -ne 0 ]; then
    echo
    echo "Qalam wurde mit einem Fehler beendet."
    read -r -p "Enter zum Schliessen..."
fi
