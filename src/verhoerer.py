"""Bekannte Verhörer korrigieren -- EINE Liste statt drei versteckte.

Ramzis Befund (01.08.2026): Qalam schreibt „öffne" als „efnes", „langsamer"
als „langzamer", „aniworld" als „anyworld". Bis jetzt stand jede Korrektur
einzeln im Code des Moduls, das sie zufällig zuerst brauchte -- „efne" in
oeffnen.ABSICHT, „langzam" in stellschrauben.WORTE. Zwei Probleme daran:

  1. Eine Korrektur, die für EIN Modul eingetragen wird, hilft den anderen
     nicht. Verstehe schliessen.py "efne" als Absicht, nur weil oeffnen.py es
     schon kennt? Nein -- jedes Modul kannte nur seine eigenen Verhörer.
  2. Jede neue Korrektur war eine Codeänderung und brauchte einen Neustart.
     Genau das ist Ramzis Aufgabe: "es fehlt ein Weg, das laufend zu tun,
     ohne dass jedes Mal ein Neustart nötig ist."

Diese Datei löst beides: EINE Korrektur-Tabelle
(`noor/werkzeuge/noor-verhoerer.json`), von JEDEM Modul an einer Stelle
angewendet, neu gelesen bei jeder Änderung -- dieselbe Bauart wie
oeffnen.katalog() und aus demselben Grund.

Wo es einzuhängen ist: möglichst früh, direkt nach dem Glätten (Umlaute weg,
klein geschrieben), bevor irgendein Modul nach Stichworten sucht. Siehe
assistant.normalisiere(), oeffnen._glaette(), stellschrauben._glaette() --
alle drei rufen jetzt korrigiere() auf.
"""
import json
import os

DATEI = os.path.join(os.path.expanduser('~'), 'noor', 'werkzeuge', 'noor-verhoerer.json')

_stand = {'zeit': None, 'tabelle': {}}


def _tabelle():
    try:
        zeit = os.path.getmtime(DATEI)
    except OSError:
        return _stand['tabelle']
    if zeit != _stand['zeit']:
        try:
            # utf-8-sig: eine BOM wuerde json scheitern lassen -- lautloser
            # Ausfall, siehe reference_noor_stellschrauben.
            with open(DATEI, encoding='utf-8-sig') as f:
                roh = json.load(f)
            flach = {}
            for k, v in roh.items():
                if k.startswith('_'):
                    continue
                if isinstance(v, list):
                    # NEUE FORM (07.08.2026): das Wort selbst ist der Schlüssel,
                    # darunter seine Verhörer als Liste --
                    # "aniworld": ["anivorld", "any world", ...].
                    # Ramzis Einwand gegen die alte Fassung: hundert flache
                    # Paare lesen sich nicht, wozu sie gehören. Hier wird die
                    # Liste zur flachen Nachschlagetabelle aufgerollt, die
                    # `korrigiere()` unten unverändert benutzt.
                    for falsch in v:
                        flach[falsch] = k
                else:
                    # ALTE FORM bleibt lesbar: falsch -> richtig, ein Paar pro
                    # Zeile. Zwei Formen gleichzeitig zu können heißt, die
                    # Datei muss nicht in einem Zug umgestellt sein -- eine
                    # halb umgestellte Tabelle ließe sonst Befehle lautlos
                    # durchfallen.
                    flach[k] = v
            _stand['tabelle'] = flach
            _stand['zeit'] = zeit
        except Exception:
            pass  # halb geschriebene Datei: beim naechsten Mal wieder versuchen
    return _stand['tabelle']


def korrigiere(geglaetteter_text):
    """Bekannte Verhörer ersetzen -- WORTWEISE, nicht als Teilstring.

    Ein Teilstring-Ersatz würde "efne" auch mitten in einem anderen Wort
    treffen. Der Text muss schon geglättet sein (klein, ohne Umlaute), sonst
    passt kein Eintrag aus der Tabelle."""
    tabelle = _tabelle()
    if not tabelle or not geglaetteter_text:
        return geglaetteter_text
    worte = geglaetteter_text.split()
    ersetzt = [tabelle.get(w, w) for w in worte]
    return ' '.join(ersetzt)
