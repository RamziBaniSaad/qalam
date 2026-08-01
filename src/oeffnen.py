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


# --- Seiten, die noch nicht im Katalog stehen -------------------------------
#
# Ramzis Frage vom 01.08.2026: „Kann ich dir sagen, öffne die
# Kleinanzeigen-Seite auf Chrome, und funktioniert das als Reflex — oder muss
# das über den Chat gehen?"
#
# Über den Chat wäre die einfache Antwort und die schlechtere: es kostet Tokens
# und dauert. Also rät der Reflex die Adresse und PRÜFT sie, bevor er etwas
# aufmacht — und schreibt sie bei Erfolg in den Katalog. Beim zweiten Mal ist
# dieselbe Seite dann sofort da, ohne Raten und ohne Netzanfrage.
#
# GERATEN WIRD NUR MIT ANSAGE. „Öffne das Fenster" darf nicht auf fenster.de
# führen. Deshalb braucht es ein Wort, das eindeutig eine Webseite meint —
# genau so, wie Ramzi es von selbst gesagt hat („die Kleinanzeigen-SEITE auf
# CHROME").
WEB_MARKER = ('seite', 'webseite', 'website', 'internetseite', 'homepage',
              'im browser', 'auf chrome', 'in chrome', 'im netz', 'im internet')

# Wörter, die zum Befehl gehören und nicht zum Namen der Seite.
FUELLER = {
    'mach', 'machs', 'mir', 'mal', 'bitte', 'auf', 'aufmachen', 'offne', 'oeffne',
    'zeig', 'zeige', 'starte', 'start', 'hol', 'bring', 'die', 'der', 'das', 'den',
    'ein', 'eine', 'einen', 'seite', 'webseite', 'website', 'internetseite',
    'homepage', 'chrome', 'browser', 'im', 'in', 'netz', 'internet', 'von', 'zu',
    'ich', 'will', 'mochte', 'du', 'kannst', 'noor', 'und', 'dann', 'jetzt', 'sonst',
}

ENDUNGEN = ('.de', '.com', '.net', '.org')


def _antwortet(url):
    """Gibt es diese Seite überhaupt? Kopfanfrage, kein Herunterladen.

    Vor dem Öffnen, nicht danach -- dieselbe Regel wie in noor-zeigen.ps1: ein
    Fenster mit „Seite nicht gefunden" ist schlimmer als keins."""
    import urllib.request
    try:
        anfrage = urllib.request.Request(url, method='HEAD',
                                         headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(anfrage, timeout=4) as a:
            return 200 <= a.status < 400
    except Exception:
        return False


def _name_aus_satz(text):
    """Was bleibt übrig, wenn man den Befehl abzieht? Das ist die Seite."""
    rest = [w for w in text.split() if w not in FUELLER and len(w) > 2]
    return ''.join(rest) if rest else None


def _merke_im_katalog(name, url):
    """Gefundene Seite in die Liste schreiben -- beim nächsten Mal ohne Raten.

    Angehängt, nicht ersetzt: die Datei gehört Ramzi, er pflegt sie mit. Und
    `_hinweis` bleibt oben stehen, damit die Erklärung nicht wegrutscht."""
    try:
        with open(KATALOG, encoding='utf-8-sig') as f:
            roh = json.load(f)
        if name in roh:
            return
        roh[name] = {'worte': [name], 'art': 'web', 'ziel': url,
                     'fenster': name, 'wo': 'ramzi', 'gelernt': True}
        with open(KATALOG, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(roh, f, ensure_ascii=False, indent=2)
        _stand['zeit'] = None          # beim nächsten Lesen frisch holen
    except Exception as e:
        print(f'[Noor] Katalog nicht ergänzt: {e}')


def rate_seite(rohtext):
    """Eine Webseite aus dem Satz raten. (Name, Adresse) oder None."""
    text = _glaette(rohtext)
    if not any(m in text for m in WEB_MARKER):
        return None
    if not any(a in text for a in ABSICHT):
        return None
    name = _name_aus_satz(text)
    if not name or len(name) < 3:
        return None
    for endung in ENDUNGEN:
        url = f'https://www.{name}{endung}'
        if _antwortet(url):
            return name, url
    return None


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
    if treffer:
        name, eintrag = treffer
        oeffne(name)
        wo = 'auf deinem Bildschirm' if eintrag.get('wo') == 'ramzi' else 'bei mir'
        return f'{name.replace("-", " ")} {wo}.'

    # Nicht im Katalog, aber eindeutig eine Webseite gemeint? Dann raten,
    # prüfen, öffnen -- und merken, damit es beim nächsten Mal sofort geht.
    geraten = rate_seite(rohtext)
    if geraten:
        name, url = geraten
        _merke_im_katalog(name, url)
        oeffne(name)
        return f'{name} auf deinem Bildschirm. Merke ich mir.'
    return None


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
