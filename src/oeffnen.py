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

Die Liste, was es gibt, steht in `noor/werkzeuge/noor-reflexe.json` — Daten,
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

import verhoerer  # noqa: E402

WERKZEUGE = os.path.join(os.path.expanduser('~'), 'noor', 'werkzeuge')
KATALOG = os.path.join(WERKZEUGE, 'noor-reflexe.json')
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
#
# „efne", „efnes", „ofne" sind keine Wörter — so schreibt Qalam Ramzis
# gesprochenes „öffne". Am 01.08.2026 ging „öffne Spotify" deshalb in den Chat
# statt zu greifen. Ein Spracherkenner verhört sich, und dafür gibt es jetzt
# EINE zentrale Korrektur (verhoerer.py, in _glaette() eingehängt) statt
# eigener Verhör-Varianten in jeder Wortliste -- "efne" kommt hier also
# bereits als "oeffne" an.
ABSICHT = (
    'mach', 'machs', 'offne', 'oeffne',
    'zeig', 'zeige', 'starte', 'start',
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
    t = re.sub(r'\s+', ' ', t).strip()
    # Bekannte Verhörer korrigieren -- siehe verhoerer.py. Damit brauchen
    # ABSICHT & Co. keine eigenen Verhör-Varianten mehr aufzulisten.
    return verhoerer.korrigiere(t)


def _glaette_katalog(text):
    """Wie `_glaette`, aber OHNE die Verhörer-Korrektur.

    Der Unterschied ist nicht kosmetisch, er hat mich am 03.08.2026 einen
    Fehlschlag gekostet. Seit die Korrekturtabelle „dododeks -> dododex"
    kennt, wurde beim Vergleich auch die KATALOGSEITE korrigiert: der
    Eintrag „dododeks" wurde zu „dododex", und „öffne dodo deks" (was Qalam
    als zwei Wörter hört und deshalb nicht korrigiert) passte auf nichts mehr.

    Die Tabelle gehört auf das, was GEHÖRT wurde. Was im Katalog steht, ist
    schon richtig geschrieben -- daran gibt es nichts zu korrigieren.
    """
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
            wg = _glaette_katalog(w)
            if not wg:
                continue
            if re.search(rf'(^|\s){re.escape(wg)}(\s|$)', text) and len(wg) > laenge:
                treffer, laenge = (name, eintrag), len(wg)
    if treffer:
        return treffer

    # Zweiter Anlauf ohne Leerzeichen -- weil Qalam Namen zerlegt.
    #
    # Ramzi hat am 03.08.2026 dreimal „öffne Dododex" gesagt und dreimal nichts
    # bekommen. Im Protokoll stand „do do dex": ein Spracherkenner setzt die
    # Wortgrenzen nach dem, was er kennt, und einen Spielenamen kennt er nicht.
    # Oben wird auf ganze Wörter geprüft, und zwischen „do do dex" und
    # „dododex" liegen genau zwei Leerzeichen.
    #
    # NUR als zweiter Anlauf und erst ab fünf Zeichen: ohne Leerzeichen wird
    # aus dem Vergleich eine Teilzeichenkette, und ein kurzes Wort steckt
    # schnell zufällig in einem längeren. „Ist das Auto da" darf nichts
    # aufmachen, „mach mir do do dex auf" schon.
    eng = text.replace(' ', '')
    for name, eintrag in katalog().items():
        for w in eintrag.get('worte', []):
            wg = _glaette_katalog(w).replace(' ', '')
            if len(wg) >= 5 and wg in eng and len(wg) > laenge:
                treffer, laenge = (name, eintrag), len(wg)
    if treffer:
        return treffer

    # Dritter Anlauf: fast richtig ist auch richtig.
    #
    # Ramzi hat denselben Namen in einer Minute als „do do dex", „do-do-dix"
    # und „doh doh dex" gesprochen, und Qalam hat jedes Mal etwas anderes
    # geschrieben. Diese Schreibweisen alle in den Katalog zu tippen ist ein
    # Wettlauf, den man nicht gewinnt -- es gibt beliebig viele, und jede
    # einzelne müsste jemand vorher erraten.
    #
    # Also wird hier verglichen, wie ÄHNLICH das Gehörte ist, statt ob es
    # gleich ist. „dohdohdex" und „dododex" sind sich zu 88 Prozent ähnlich,
    # „dododix" zu 93 -- ein Zufallstreffer kommt da nicht mehr hin.
    #
    # Warum das trotzdem kein Fenster aufreisst, das niemand wollte: bis
    # hierher ist schon geprüft, dass ein Absichtswort dabeisteht („mach mir",
    # „öffne") und dass der Satz kurz ist. Und erst ab sechs Zeichen -- kurze
    # Wörter sind sich zu leicht ähnlich.
    # DER VORFILTER IST HIER KEIN FEINSCHLIFF, SONDERN DER UNTERSCHIED ZWISCHEN
    # brauchbar und unbrauchbar: ohne ihn hat dieser Anlauf 487 ms je Satz
    # gebraucht, weil er jeden Ausschnitt gegen jedes Wort im Katalog gehalten
    # hat. Das Ohr wäre in dieser Zeit taub gewesen -- bei einem Modul, dessen
    # ganzer Sinn „schneller als der Chat" ist.
    #
    # Die Abkürzung: zwei Wörter, die sich zu 85 Prozent ähneln, teilen sich
    # fast immer mindestens drei Buchstaben am Stück. Diese Prüfung kostet
    # nichts und wirft weit über 99 von 100 Kandidaten sofort weg.
    import difflib
    bestes = 0.0
    for name, eintrag in katalog().items():
        for w in eintrag.get('worte', []):
            wg = _glaette_katalog(w).replace(' ', '')
            if len(wg) < 6:
                continue
            if not any(wg[i:i + 3] in eng for i in range(len(wg) - 2)):
                continue
            # Der Name kann im Satz länger oder kürzer angekommen sein, also
            # werden Ausschnitte um seine Länge herum verglichen.
            for breite in range(max(6, len(wg) - 2), len(wg) + 3):
                for i in range(len(eng) - breite + 1):
                    aehnlich = difflib.SequenceMatcher(
                        None, wg, eng[i:i + breite]).ratio()
                    if aehnlich >= 0.85 and aehnlich > bestes:
                        treffer, bestes = (name, eintrag), aehnlich
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
            # Der Insel Bescheid sagen -- Ramzi soll oben sehen, was aufgeht,
            # ohne dass ich es ihm zusaetzlich erzaehlen muss.
            try:
                import ereignis
                ereignis.melde('Fenster geöffnet: %s' % name, 'fenster')
            except Exception:
                pass
            # CREATE_NO_WINDOW ist hier kein Detail, sondern der Unterschied
            # zwischen Werkzeug und Stoerung. Das Ohr laeuft unter pythonw, also
            # ohne Konsole -- jeder PowerShell-Aufruf macht sich deshalb ein
            # EIGENES Fenster auf, und das stand bei Ramzi mitten auf dem
            # Bildschirm, ein paar Sekunden lang, bei jedem Befehl. Sein Wort
            # dazu: "dann kann ich auf einmal nichts mehr sehen."
            subprocess.run(['powershell', '-NoProfile', '-NonInteractive',
                            '-ExecutionPolicy', 'Bypass', '-File', SKRIPT, name],
                           capture_output=True, timeout=60,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
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
