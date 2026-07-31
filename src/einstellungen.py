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
            with open(DATEI, encoding='utf-8') as f:
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
