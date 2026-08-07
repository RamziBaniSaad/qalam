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

_stand = {'zeit': None, 'einzeln': {}, 'folgen': {}, 'laengste': 1}


def _tabelle():
    """Die Datei als zwei Nachschlagetabellen: Einzelwörter und Wortfolgen.

    In der Datei steht das RICHTIGE Wort als Schlüssel und darunter die Liste
    seiner Verhörer -- "aniworld": ["anivorld", "any world", ...]. Hier wird
    das umgedreht, denn nachgeschlagen wird ja das Gehörte.

    Zwei Tabellen, weil ein Verhörer auch aus mehreren Wörtern bestehen kann:
    Qalam schreibt „Netflix" als „net fliks" und „lautstärke" als „laut steg".
    Solche Einträge sind über einen Vergleich Wort für Wort nicht erreichbar --
    sie stehen zwischen zwei Leerzeichen, nicht auf einem.

    Ein einzelnes Paar `"falsch": "richtig"` wird ebenfalls gelesen, damit
    Ramzi eine Korrektur im Vorbeigehen eintragen kann, ohne die Form zu
    treffen."""
    try:
        zeit = os.path.getmtime(DATEI)
    except OSError:
        return _stand
    if zeit != _stand['zeit']:
        try:
            # utf-8-sig: eine BOM wuerde json scheitern lassen -- lautloser
            # Ausfall, siehe reference_noor_stellschrauben.
            with open(DATEI, encoding='utf-8-sig') as f:
                roh = json.load(f)
            einzeln, folgen, laengste = {}, {}, 1
            for richtig, verhoert in roh.items():
                if richtig.startswith('_'):
                    continue
                for falsch in (verhoert if isinstance(verhoert, list) else [richtig]):
                    ziel = richtig if isinstance(verhoert, list) else verhoert
                    teile = falsch.split()
                    if len(teile) > 1:
                        folgen[' '.join(teile)] = ziel
                        laengste = max(laengste, len(teile))
                    elif teile:
                        einzeln[teile[0]] = ziel
            _stand['einzeln'] = einzeln
            _stand['folgen'] = folgen
            _stand['laengste'] = laengste
            _stand['zeit'] = zeit
        except Exception:
            pass  # halb geschriebene Datei: beim naechsten Mal wieder versuchen
    return _stand


def korrigiere(geglaetteter_text):
    """Bekannte Verhörer ersetzen -- WORTWEISE, nicht als Teilstring.

    Ein Teilstring-Ersatz würde "efne" auch mitten in einem anderen Wort
    treffen. Der Text muss schon geglättet sein (klein, ohne Umlaute), sonst
    passt kein Eintrag aus der Tabelle.

    Die längste passende Wortfolge gewinnt: steht „net fliks" in der Tabelle,
    darf nicht vorher „net" einzeln ersetzt worden sein."""
    stand = _tabelle()
    einzeln, folgen = stand['einzeln'], stand['folgen']
    if not geglaetteter_text or not (einzeln or folgen):
        return geglaetteter_text
    worte = geglaetteter_text.split()
    ersetzt = []
    i = 0
    while i < len(worte):
        treffer = None
        for laenge in range(min(stand['laengste'], len(worte) - i), 1, -1):
            folge = ' '.join(worte[i:i + laenge])
            if folge in folgen:
                treffer = (folgen[folge], laenge)
                break
        if treffer is None:
            treffer = (einzeln.get(worte[i], worte[i]), 1)
        ersetzt.append(treffer[0])
        i += treffer[1]
    return ' '.join(ersetzt)
