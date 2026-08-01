"""Die Stellschrauben, die Ramzi im Betrieb ändern können soll.

Eine kleine JSON-Datei statt der grossen `config.yaml`: Sie wird von mehreren
Prozessen gelesen (Ohr, Stimme, Stop-Hook) und soll sich ändern lassen, ohne
irgendetwas neu zu starten. Deshalb wird sie bei jedem Zugriff neu gelesen,
wenn sie sich geändert hat -- das kostet nichts und erspart einen Neustart für
jeden Schieberegler.

Geändert wird sie von der Tafel (Schieberegler + Häkchen) oder von Hand.
"""
import json
import os

HIER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATEI = os.path.join(HIER, 'noor-einstellungen.json')

STANDARD = {
    # Sprechtempo. 1.0 = wie das Modell trainiert wurde. Ramzi findet das zu
    # langsam; über 1.6 klingt es gehetzt.
    'tempo': 1.25,
    # Lautstärke der Stimme, 0.0 bis 1.5.
    'lautstaerke': 1.0,
    # Die Tonzeichen (wach, Reflex, Brücke, Fenster ...) ganz abschaltbar.
    'toene': True,
    # Wie lange Ramzi schweigen darf, ohne dass der Satz als beendet gilt.
    # 600 ms waren viel zu wenig -- er konnte keinen Satz zu Ende sprechen.
    'stille_ms': 1600,
    # Wie lange nach einer Antwort ein Folgesatz OHNE Weckwort gilt.
    'folge_sekunden': 15,
    # Wie lange der Untertitel-Streifen stehen bleibt, in Sekunden.
    # 0 = Untertitel ganz aus. Damit ist der Regler gleichzeitig der Schalter.
    'untertitel_sekunden': 10,

    # ------------------------------------------------------------------ Feedback
    # Ramzis Auftrag vom 01.08.2026 nachts: KEIN Schalter plus Skala mehr,
    # sondern je Sache EIN Regler von 0 bis 100 -- und 0 heißt aus. Zwei
    # Angaben für dieselbe Sache konnten sich widersprechen ("Ton an" bei
    # "Lautstärke 0"), eine Angabe kann das nicht. `untertitel_sekunden`
    # oben macht das seit dem 31.07. schon vor; hier gilt es für alles.
    #
    # Aufgebaut in zwei Ebenen, genau wie die Tafel es zeigt: `lautstaerke`
    # ist der HAUPTREGLER für alles Hörbare (Töne UND Stimme), die Werte hier
    # sind Anteile davon. Gerechnet wird in ton.py bzw. voice_output.py.
    #
    # Flach und nicht verschachtelt, obwohl das erst reizvoller aussieht:
    # `setze()` und `alle()` unten filtern gegen STANDARD, und bei einem
    # verschachtelten Wert würde ein unvollständiger Teil-Baum aus der Datei
    # die Vorgaben darunter mitreißen. Flach kostet ein paar Zeilen mehr und
    # kann diesen Fehler nicht machen.
    'laut_stimme':         100,   # meine Stimme (darf über 100, Piper verstärkt)
    'laut_arbeitet':       100,   # der Ton im Moment einer Aktion
    'laut_wach':           100,   # Weckwort erkannt
    'laut_bruecke':        100,   # Auftrag ist abgeschickt
    'laut_pause_an':       100,   # Denkpause beginnt
    'laut_pause_aus':      100,   # Denkpause endet
    'laut_nichts':         100,   # abgebrochen / nichts verstanden
    'laut_reflex':         100,   # ein lokaler Reflex hat gegriffen
    'laut_fenster_auf':    100,   # ein Fenster ist aufgegangen
    'laut_fenster_zu':     100,   # ein Fenster ist zugegangen
    'laut_aufnahme_start': 100,   # Qalam beginnt aufzunehmen
    'laut_aufnahme_ende':  100,   # Qalam ist fertig
    'laut_countdown':      100,   # die Countdown-Klänge

    # Das Sichtbare. Ramzi hielt hier nur an/aus für möglich -- Glanz und Blitz
    # haben aber je einen Helligkeitswert, also sind auch sie Regler mit
    # 0 = aus. Dieselbe Bauform wie beim Ton, kein Sonderfall.
    'bild_glanz':           30,   # der leise Dauerglanz, solange ich arbeite
    'bild_blitz':          100,   # der kräftige Blitz im Moment einer Aktion

    # Wann Blitz und Ton überhaupt kommen:
    #   'alle'     -- bei JEDEM Werkzeugaufruf, auch bei unsichtbaren
    #   'sichtbar' -- nur wenn dabei etwas auf dem Bildschirm passiert
    #                 (eine Datei geändert, ein Fenster aufgemacht)
    'feedback_modus': 'alle',

    # Das Aufnahme-Fenster während der Aufnahme gar nicht zeigen. Ramzi hält
    # das selbst für eine Einstellung, die niemand braucht -- sie ist drin,
    # weil sie fast nichts kostet und in einem verkauften Produkt fehlen würde.
    'bild_fenster_aus': False,
}

_stand = {'zeit': None, 'werte': dict(STANDARD)}


def alle():
    """Aktuelle Werte. Liest die Datei nur neu, wenn sie sich geändert hat."""
    try:
        zeit = os.path.getmtime(DATEI)
    except OSError:
        return _stand['werte']

    if zeit != _stand['zeit']:
        try:
            # utf-8-sig und nicht utf-8: eine BOM am Dateianfang laesst json
            # scheitern, und dieses `except` unten wuerde den Fehler still
            # schlucken -- Qalam spraeche dann mit den Vorgabewerten weiter,
            # obwohl Ramzis echte Werte in der Datei stehen. Am 01.08.2026
            # genau so passiert, als ein PowerShell-Aufruf die Datei mit
            # `Set-Content -Encoding utf8` geschrieben hat (das setzt unter
            # PowerShell 5.1 immer eine BOM). Eine BOM zu lesen kostet nichts;
            # sie nicht zu lesen kostet eine unsichtbare Fehlfunktion.
            with open(DATEI, encoding='utf-8-sig') as f:
                gelesen = json.load(f)
            werte = dict(STANDARD)
            werte.update({k: v for k, v in gelesen.items() if k in STANDARD})
            _stand['werte'] = werte
            _stand['zeit'] = zeit
        except Exception:
            pass
    return _stand['werte']


def hole(name):
    return alle().get(name, STANDARD.get(name))


def setze(**neue):
    """Werte ändern und ablegen. Unbekannte Namen werden verworfen."""
    werte = dict(alle())
    werte.update({k: v for k, v in neue.items() if k in STANDARD})
    try:
        with open(DATEI, 'w', encoding='utf-8') as f:
            json.dump(werte, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    _stand['werte'] = werte
    _stand['zeit'] = None      # beim nächsten Lesen frisch holen
    return werte


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        paare = {}
        for teil in sys.argv[1:]:
            k, _, v = teil.partition('=')
            if v.lower() in ('true', 'false'):
                paare[k] = v.lower() == 'true'
            else:
                try:
                    paare[k] = float(v) if '.' in v else int(v)
                except ValueError:
                    paare[k] = v
        print(json.dumps(setze(**paare), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(alle(), ensure_ascii=False, indent=2))
