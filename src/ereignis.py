"""Eine Zeile an die Insel schicken -- aus Python heraus.

Gegenstueck zu noor/werkzeuge/noor-ereignis.ps1, gleiche Datei, gleiches
Format. Warum eine eigene Fassung statt den PowerShell-Aufruf zu starten: ein
Prozessstart kostet hier mehr als das Ereignis wert ist, und diese Stellen
melden oft mehrmals hintereinander (Fenster auf, Fenster zu, gesprochen).

Absichtlich ohne Abhaengigkeiten und ohne Ausnahmen nach aussen: eine Anzeige
darf niemals der Grund sein, warum etwas anderes stehenbleibt.
"""
import json
import os
import tempfile
import time

DATEI = os.path.join(tempfile.gettempdir(), 'noor-ereignisse.jsonl')
HOECHSTALTER = 600
HOECHSTENS = 40


def melde(text, art='info', neben=''):
    text = (text or '').strip()
    if not text:
        return
    try:
        jetzt = int(time.time())
        alt = []
        if os.path.exists(DATEI):
            with open(DATEI, encoding='utf-8') as f:
                for zeile in f:
                    zeile = zeile.strip()
                    if not zeile:
                        continue
                    try:
                        if json.loads(zeile).get('zeit', 0) > jetzt - HOECHSTALTER:
                            alt.append(zeile)
                    except Exception:
                        continue
        alt = alt[-HOECHSTENS:]
        alt.append(json.dumps({'zeit': jetzt, 'art': art, 'text': text,
                               'neben': neben}, ensure_ascii=False))
        with open(DATEI, 'w', encoding='utf-8') as f:
            f.write('\n'.join(alt) + '\n')
    except Exception:
        return
