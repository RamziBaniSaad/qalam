"""„Mach mir YouTube auf" — lokal, ohne dass es Noor im Chat erreicht.

Ramzis Anlass, wörtlich (01.08.2026): „Als ich essen wollte, wollte ich YouTube
öffnen, und das war ein bisschen schwer für mich, weil meine Hände dreckig
waren. Da hätte ich mir gewünscht, dass ich dir sagen kann, du sollst YouTube
öffnen — auf meinem Bildschirm direkt, damit ich nur noch kurz ein Video
anklicke."

WARUM DIE ERKENNUNG HIER LIEGT UND DAS ÖFFNEN WOANDERS: Nachschlagen ist eine
Sache von Mikrosekunden, ein PowerShell-Start kostet eine halbe Sekunde und ein
neues Fenster ein bis drei. Würde ich für JEDEN Satz das Skript befragen, wäre
jeder Reflex langsamer geworden — auch die, die mit Fenstern nichts zu tun
haben. Also entscheidet Python hier, ob überhaupt etwas gemeint ist, und ruft
das Skript nur bei einem Treffer.

Die Liste, was es gibt, steht in `noor/werkzeuge/noor-katalog.json` — Daten,
kein Code. Ramzis Sorge war, dass das Skript riesig wird, wenn alles Mögliche
hineinsoll; so kann es das nicht: eine neue Sache ist eine Zeile.

Prüfen ohne Mikrofon:
    python -m src.oeffnen --probe
    python -m src.oeffnen "mach mir mal youtube auf"
"""
import json
import os
import re
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WERKZEUGE = os.path.join(os.path.expanduser('~'), 'noor', 'werkzeuge')
KATALOG = os.path.join(WERKZEUGE, 'noor-katalog.json')
SKRIPT = os.path.join(WERKZEUGE, 'noor-auf.ps1')

# Bis zu wie vielen Wörtern eine Äußerung als Öffnen-Befehl gilt. Dieselbe
# Begründung wie in stellschrauben.py: in einem langen Auftrag kommt leicht mal
# „Anime" oder „Ordner" vor, und ein Fenster, das grundlos aufgeht, ist genau
# die Art Fehlgriff, die Ramzi stört.
OEFFNEN_MAX_WOERTER = 14

# Ohne eines dieser Wörter passiert nichts.
#
# „Ich habe gestern ein Anime geschaut" ist eine Erzählung, kein Auftrag. Der
# Unterschied ist nicht das Stichwort, sondern die Absicht -- und die steht in
# genau diesen Wörtern. Lieber ein verpasster Befehl (der landet bei Noor und
# wird beantwortet) als ein Fenster, das niemand wollte.
ABSICHT = (
    'mach', 'machs', 'offne', 'oeffne', 'zeig', 'zeige', 'starte', 'start',
    'hol', 'gehe auf', 'geh auf', 'ruf', 'bring', 'ich will', 'ich mochte',
    'lass uns', 'wechsel', 'aufmachen', 'anmachen',
)

# Was NICHT gemeint sein kann, auch wenn ein Stichwort passt.
#
# „Mach die Musik leiser" oder „mach das Fenster zu" enthalten Absicht und
# manchmal auch ein Stichwort -- gemeint ist aber das Gegenteil vom Aufmachen.
GEGENTEIL = ('zu machen', 'zumachen', 'schliess', 'zu ', 'weg machen', 'wegmachen',
             'beenden', 'aus machen', 'ausmachen', 'minimier', 'leiser', 'lauter')


def _glaette(text):
    t = (text or '').lower()
    for a, b in (('ä', 'a'), ('ö', 'o'), ('ü', 'u'), ('ß', 'ss')):
        t = t.replace(a, b)
    t = re.sub(r'[^\w ]+', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


_stand = {'zeit': None, 'katalog': {}}


def katalog():
    """Die Liste, neu gelesen nur wenn die Datei sich geändert hat.

    Ramzi soll sie erweitern können, ohne dass etwas neu startet -- dieselbe
    Bauart wie einstellungen.py, und aus demselben Grund."""
    try:
        zeit = os.path.getmtime(KATALOG)
    except OSError:
        return _stand['katalog']
    if zeit != _stand['zeit']:
        try:
            # utf-8-sig: eine BOM würde json scheitern lassen, und das wäre hier
            # ein lautloser Ausfall -- siehe reference_noor_stellschrauben.
            with open(KATALOG, encoding='utf-8-sig') as f:
                roh = json.load(f)
            _stand['katalog'] = {k: v for k, v in roh.items() if not k.startswith('_')}
            _stand['zeit'] = zeit
        except Exception:
            pass
    return _stand['katalog']


def verstehe(rohtext):
    """Ist das ein „mach mir X auf"? Dann (Name, Eintrag), sonst None.

    Es wird NICHT geöffnet -- das tut oeffne(). Getrennt, damit sich das
    Erkennen ohne Nebenwirkung prüfen lässt."""
    text = _glaette(rohtext)
    if not text:
        return None
    worte = text.split()
    if len(worte) > OEFFNEN_MAX_WOERTER:
        return None
    if any(g in text for g in GEGENTEIL):
        return None
    if not any(a in text for a in ABSICHT):
        return None

    # Das längste passende Wort gewinnt -- wer länger passt, passt genauer.
    treffer, laenge = None, 0
    for name, eintrag in katalog().items():
        for w in eintrag.get('worte', []):
            wg = _glaette(w)
            if not wg:
                continue
            if re.search(rf'(^|\s){re.escape(wg)}(\s|$)', text) and len(wg) > laenge:
                treffer, laenge = (name, eintrag), len(wg)
    return treffer


def oeffne(name):
    """Das Skript rufen, im eigenen Faden.

    Im Hintergrund, weil ein Fenster ein bis drei Sekunden braucht -- so lange
    darf das Ohr nicht taub sein, sonst geht das nächste „Noor, stopp"
    verloren. Dieselbe Begründung wie bei _an_noor() in assistant.py."""
    def _lauf():
        try:
            subprocess.run(['powershell', '-NoProfile', '-NonInteractive',
                            '-ExecutionPolicy', 'Bypass', '-File', SKRIPT, name],
                           capture_output=True, timeout=60)
        except Exception as e:
            print(f'[Noor] Öffnen fehlgeschlagen: {e}')
    threading.Thread(target=_lauf, daemon=True).start()


def mach(rohtext):
    """Erkennen und tun. Gibt den Antwortsatz zurück oder None."""
    treffer = verstehe(rohtext)
    if not treffer:
        return None
    name, eintrag = treffer
    oeffne(name)
    wo = 'auf deinem Bildschirm' if eintrag.get('wo') == 'ramzi' else 'bei mir'
    return f'{name.replace("-", " ")} {wo}.'


PROBEN = [
    'mach mir mal YouTube auf',
    'öffne Spotify',
    'ich will ein Anime schauen',
    'zeig mir den Explorer',
    'mach das Sitzungsprotokoll auf',
    'starte Spotify',
    'mach den Noor-Ordner auf',
    # Diese hier dürfen NICHT greifen:
    'ich habe gestern ein Anime geschaut',
    'mach die Musik leiser',
    'mach das Fenster zu',
    'wie spät ist es',
    'ich muss noch die Dateien von gestern durchgehen und schauen, ob im Ordner '
    'noch etwas fehlt, bevor ich schlafen gehe',
]


def _probe():
    breite = max(len(s) for s in PROBEN)
    for satz in PROBEN:
        t = verstehe(satz)
        print(f'{satz:<{breite}}  ->  '
              f'{t[0] + " (" + (t[1].get("wo") or "ich") + ")" if t else "— (geht an Noor)"}')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--probe':
        _probe()
    elif len(sys.argv) > 1:
        print(mach(' '.join(sys.argv[1:])) or '— kein Öffnen-Befehl')
    else:
        print(__doc__)
