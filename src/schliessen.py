"""„Mach das wieder zu" — das Gegenstück zu oeffnen.py.

Ramzis Befund nach dem Testen (01.08.2026): „Das Aufmachen läuft, wir müssen
nur gucken, dass wir auch Schließfenster haben." Es gab bis dahin nur den
Umschalter Strg+Alt+W und `noor-zeigen -Modus frei` — beides Handgriffe, kein
Satz.

WESSEN FENSTER, UND WARUM DAS DIE EIGENTLICHE FRAGE IST

In noor-zeigen.ps1 steht seit dem 01.08.2026 eine Regel, die ich teuer
gelernt habe: was auf RAMZIS Bildschirm landet, gehört ihm und wird von
meinem Aufräumen nicht angefasst. Ein Video, das ich ihm auf Bitte aufmache,
darf ich nicht später beiläufig wieder zumachen — genau das ist an dem Abend
passiert, sein YouTube ging beim nächsten „frei" mit.

Der letzte Halbsatz jener Regel lautet aber: „Zumachen tut er es selbst --
oder er sagt es mir." Und dieser Fall ist hier. Deshalb:

    „mach alles zu", „räum auf"      -> nur MEIN Bildschirm (wie bisher)
    „mach YouTube zu"                -> auch auf SEINEM, weil er es BENENNT

Beiläufig ist etwas anderes als benannt. Ohne diese Unterscheidung wäre der
Reflex entweder gefährlich (räumt seinen Bildschirm leer) oder halb nutzlos
(kann das eine Fenster nicht zumachen, um das es ihm geht).

Prüfen ohne Mikrofon:
    python -m src.schliessen --probe
    python -m src.schliessen "mach mal youtube zu"
"""
import os
import re
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oeffnen  # noqa: E402  -- Katalog und _glaette werden geteilt

WERKZEUGE = os.path.join(os.path.expanduser('~'), 'noor', 'werkzeuge')
SKRIPT_ALLES = os.path.join(WERKZEUGE, 'noor-links-zu.ps1')
SKRIPT_EINS = os.path.join(WERKZEUGE, 'noor-zu.ps1')

# Dieselbe Deckelung wie beim Öffnen und aus demselben Grund: in einem langen
# Auftrag kommt leicht mal „zumachen" vor, ohne dass ein Fenster gemeint ist.
MAX_WOERTER = 14

# Ohne eines dieser Wörter passiert nichts. „zu" allein steht bewusst NICHT
# hier -- das Wort kommt in jedem zweiten Satz vor („zu spät", „zu dir").
ABSICHT = (
    'zumachen', 'zu machen',
    'schliess', 'schliesse', 'schliessen',
    'weg damit', 'wegmachen', 'weg machen', 'raum auf', 'raume auf',
    'aufraumen', 'beende', 'beenden',
)

# Die deutsche Verbklammer, und ohne sie greift fast nichts: „mach das wieder
# ZU" -- zwischen Verb und Partikel steht der halbe Satz, ein Teilstring
# „mach zu" trifft das nie. Am 01.08.2026 sind mir daran vier von sechs
# Proben durchgefallen.
#
# Erkannt wird deshalb am SATZENDE: steht dort „zu" und vorne ein Machen-Wort,
# ist es ein Schließbefehl. „mach mir YouTube auf" endet auf „auf" und bleibt
# damit außen vor, „das ist mir zu viel" endet nicht auf „zu".
KLAMMER_VORNE = ('mach', 'machs', 'tu', 'tue', 'kannst', 'wurdest', 'bitte')

# „mach ALLES zu" -- kein einzelnes Fenster gemeint, sondern mein Bildschirm.
ALLES = ('alles', 'alle fenster', 'den bildschirm', 'deinen bildschirm',
         'bei dir', 'auf', 'sauber', 'leer')

# Wörter, die zum Befehl gehören und nicht zum Namen des Fensters.
FUELLER = {
    'mach', 'machs', 'mal', 'bitte', 'zu', 'das', 'die', 'der', 'den', 'wieder',
    'zumachen', 'schliess', 'schliesse', 'schliessen', 'weg', 'wegmachen',
    'damit', 'noor', 'du', 'kannst', 'ich', 'will', 'mochte', 'jetzt', 'nochmal',
    'fenster', 'seite', 'ein', 'eine', 'einen', 'und', 'dann', 'bei', 'mir', 'dir',
}


def verstehe(rohtext):
    """None, 'alles', oder der gesuchte Name als Text.

    Trennt bewusst nicht in Katalog-Treffer und Rest wie oeffnen.verstehe():
    beim Schließen zählt der FENSTERTITEL, und der muss nicht im Katalog
    stehen. „mach das Kleinanzeigen-Fenster zu" soll auch dann greifen, wenn
    die Seite dort nie eingetragen wurde."""
    text = oeffnen._glaette(rohtext)
    if not text:
        return None
    worte = text.split()
    if len(worte) > MAX_WOERTER:
        return None

    eindeutig = any(a in text for a in ABSICHT)
    # Verbklammer: „mach das wieder ZU" -- Partikel am Satzende, Verb vorn.
    klammer = (worte[-1] == 'zu' and any(w in KLAMMER_VORNE for w in worte))
    if not (eindeutig or klammer):
        return None

    # Erst den Katalog fragen: steht dort ein passender Name, ist das die
    # genauere Auskunft als ein aus dem Satz geschnittenes Wort. Das längste
    # passende gewinnt, wie beim Öffnen.
    treffer, laenge = None, 0
    for name, eintrag in oeffnen.katalog().items():
        for w in eintrag.get('worte', []):
            wg = oeffnen._glaette(w)
            if wg and re.search(rf'(^|\s){re.escape(wg)}(\s|$)', text) and len(wg) > laenge:
                treffer, laenge = name, len(wg)
    if treffer:
        return treffer

    # „alles" erst NACH dem Katalog prüfen: sonst schlüge „mach alles zu"
    # richtig an, aber „mach das Aufräum-Fenster zu" ebenfalls.
    if any(a in text.split() for a in ALLES):
        return 'alles'

    rest = [w for w in text.split() if w not in FUELLER]
    if rest:
        # Das längste übrige Wort ist am ehesten der Name -- kurze Reste sind
        # meist Füllwörter, die in der Liste oben fehlen.
        return max(rest, key=len)

    # Nichts benannt: „mach zu" allein meint meinen Bildschirm.
    return 'alles'


def _lauf(befehl):
    """Im Hintergrund, ohne Fenster -- siehe reference_noor_keine_fenster."""
    def _tu():
        try:
            subprocess.run(befehl, capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except Exception as e:
            print(f'[Noor] Schließen fehlgeschlagen: {e}')
    threading.Thread(target=_tu, daemon=True).start()


def mach(rohtext):
    """Erkennen und tun. Gibt den Antwortsatz zurück oder None."""
    ziel = verstehe(rohtext)
    if not ziel:
        return None

    if ziel == 'alles':
        _lauf(['powershell', '-NoProfile', '-NonInteractive',
               '-ExecutionPolicy', 'Bypass', '-File', SKRIPT_ALLES])
        return 'Mache bei mir alles zu.'

    _lauf(['powershell', '-NoProfile', '-NonInteractive',
           '-ExecutionPolicy', 'Bypass', '-File', SKRIPT_EINS, '-Was', ziel])
    return f'{ziel.replace("-", " ")} mache ich zu.'


PROBEN = [
    'mach das wieder zu',
    'mach alles zu',
    'mach YouTube zu',
    'schließ mal Spotify',
    'räum auf',
    'mach das Kleinanzeigen-Fenster zu',
    # Diese hier dürfen NICHT greifen:
    'wie spät ist es',
    'mach mir YouTube auf',
    'das ist mir zu viel',
    'ich komme zu dir',
]


def _probe():
    breite = max(len(s) for s in PROBEN)
    for s in PROBEN:
        print(f'{s:<{breite}}  ->  {verstehe(s)}')


if __name__ == '__main__':
    if '--probe' in sys.argv:
        _probe()
    elif len(sys.argv) > 1:
        print(mach(' '.join(sys.argv[1:])))
    else:
        print(__doc__)
