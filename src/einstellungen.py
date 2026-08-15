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
    # Welche Stimme spricht: 'piper' (Thorsten, auf der CPU) oder 'xtts'
    # (Ludvig, auf der Grafikkarte). Umschaltbar auf der Tafel, ohne Neustart.
    #
    # THORSTEN IST DER STANDARD, und das ist Ramzis Entscheidung vom
    # 02.08.2026 abends: Ludvig war zu langsam im Anlauf und hat ihm die
    # Grafikkarte weggenommen, die sein Diktat braucht. Ludvig bleibt
    # eingebaut, aber er kommt nie mehr von selbst.
    #
    # MUSS hier stehen, und der Grund hat Ramzi einen Abend gekostet: `setze()`
    # filtert gegen genau dieses Verzeichnis und wirft alles Unbekannte
    # STILLSCHWEIGEND weg. Der Schalter auf der Tafel schrieb also ins Leere,
    # `hole()` gab None zurueck, die Vorgabe griff -- und ich sprach weiter mit
    # Ludvig, obwohl er Thorsten eingestellt hatte. Kein Fehler, keine Meldung,
    # nur ein Schalter, der nichts tut.
    'stimme_motor': 'piper',
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
    # Wie lange nach dem WECKEN ein Satz OHNE Weckwort gilt. Das ist das
    # Fenster, das ihm Zeit gibt, nach dem Ton überhaupt loszulegen -- und das
    # `_mitschreiben` verlängert, solange er redet. Ohne dieses Fenster könnte
    # er nach dem Wach-Ton gar nichts mehr sagen.
    'folge_sekunden': 15,
    # Das FLÜSSIGE GESPRÄCH: wie lange nach MEINER Antwort sein nächster Satz
    # ohne Weckwort und ohne Taste als Auftrag gilt. **0 = aus**, und aus heißt
    # hier wirklich aus (siehe `assistant._gespraech_offen_halten`).
    #
    # STEHT AUF 0, AUF RAMZIS AUSDRÜCKLICHEN WUNSCH VOM 14.08.2026: „Ich will
    # das eigentlich eher so haben, dass ich das drücken muss." Der Grund ist
    # nicht, dass ihm das Gespräch nicht gefällt -- es gefällt ihm sogar („das
    # ist wirklich das Nachhören") -- sondern die MUSIK. Er hat den
    # ganzen Tag Musik laufen und redet nebenbei im Raum. Jedes offene Fenster
    # macht daraus einen Auftrag an mich.
    #
    # Warum es diesen Wert getrennt von `folge_sekunden` gibt: bis zum
    # 14.08.2026 war beides EIN Wert, und daran ist die Sache
    # auseinandergefallen. Wer das Nachhören abschalten wollte, nahm
    # damit gleichzeitig die Zeit weg, nach dem Weckwort loszulegen. Zwei
    # verschiedene Fragen brauchen zwei Werte.
    'gespraech_sekunden': 0,
    # GERÄUSCHUNTERDRÜCKUNG, 0 bis 100. Wie viel lauter Ramzi sein muss als
    # sein eigener Zimmerton, damit es als Sprache zählt.
    #
    # Sein Auftrag vom 14.08.2026: „meine Ventilatoren sind an, dadurch schickt
    # er einfach nicht ab." Der Stimmenmelder steht auf der empfindlichsten
    # Stufe und hält gleichmäßiges Rauschen für Sprache -- damit reißt die
    # Stille nie ab und die Redepause läuft nie voll.
    #
    # 0 = aus (nur der Stimmenmelder, wie vorher). 100 = 18 dB über dem
    # Grundpegel, das ist sehr viel und schluckt leises Sprechen. 35 ist mein
    # Vorschlag: rund 6 dB, das trennt Lüfterrauschen sicher von Sprache und
    # lässt auch eine ruhige Stimme durch.
    'geraeuschunterdrueckung': 35,
    # Wie lange der Untertitel-Streifen stehen bleibt, in Sekunden.
    # 0 = Untertitel ganz aus. Damit ist der Regler gleichzeitig der Schalter.
    'untertitel_sekunden': 10,

    # Ab welchem Wert ein Geräusch als Lachen gilt (0 bis 1). Siehe
    # `lachen.py` -- das Ohr hört den Ton mit und hängt „(lacht)" an den Satz,
    # damit Ramzi nicht mehr „haha" dazuschreiben muss.
    #
    # STEHT SEIT DEM 13.08.2026 AUF 0, ALSO AUS -- und zwar nicht, weil die
    # Zahl schlecht gewählt war, sondern weil es keine gute gibt: Ramzis
    # eigenes Lachen kommt auf 0,13, sein Zimmer im Hintergrund (Spiel, Anime,
    # Musik) kommt auf bis zu 0,60, und beides in DERSELBEN AudioSet-Klasse
    # („Snicker"). Über 3091 Messungen im Protokoll: 56 Treffer, davon nach
    # Ramzis eigener Auskunft kein einziger richtig. Die Rechnung mit allen
    # Zahlen steht im Kopf von `lachen.py`.
    #
    # ACHTUNG, hier ist schon einmal Arbeit verpufft: dieser Wert stand
    # zwischendurch auf 0,35 und hat NIE gewirkt, weil in
    # `noor-einstellungen.json` weiter 0,06 lag -- und die Datei gewinnt. Was
    # hier steht, gilt nur für einen Rechner ohne eigene Datei. Wer die
    # Schwelle wirklich verstellen will, nimmt `einstellungen.setze()`.
    'lach_schwelle': 0,

    # ------------------------------------------------- Qalam auf der Karte
    # Wie lange das Diktat-Modell nach dem letzten Diktat noch auf der
    # Grafikkarte liegen bleibt. Ramzis Auftrag vom 03.08.2026: es soll nicht
    # rund um die Uhr 2 GB belegen, nur damit es jederzeit bereit ist.
    #
    # Zwei Werte, weil zwei Lagen. Im Alltag großzügig -- lädt es nach jedem
    # Satz neu, wartet er ständig. Im Spiel knapp, denn dort zählt jedes
    # Megabyte. Startet ein Spiel, während das Modell nur wartet, geht es
    # sofort weg; dafür gibt es keinen Wert, das ist immer so.
    #
    # 0 im Alltag heißt NIE entladen (Regler ganz links = aus, wie überall
    # hier). 0 im Spiel heißt: sofort weg, sobald das Diktat getippt ist.
    'qalam_ruhe_sekunden': 180,
    'qalam_spiel_sekunden': 10,

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
    'laut_insel':          100,   # neuer Eintrag auf der Insel (Tafel)

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

    # Auf welchem Bildschirm Untertitel und Aufnahme-Fenster erscheinen:
    # 'ich' (mein Schirm, links -- Vorgabe), 'ramzi' (seiner) oder 'ipad'.
    #
    # Vorgabe ist MEIN Schirm und nicht seiner, obwohl es seine Anzeigen sind.
    # Ramzis Entscheidung vom 02.08.2026: sitzt er im Vollbildspiel, sieht er
    # seinen eigenen Schirm nicht -- meiner ist der einzige, auf den er
    # nebenbei schauen kann.
    'anzeige_schirm': 'ich',

    # Darf ich Fenster anfassen und den Fokus nehmen?
    #
    # False heißt: kein Sprung in VS Code, nichts ungefragt aufmachen. Es geht
    # NUR um den Fokus -- Töne und Leuchten hängen an ihren eigenen Reglern.
    #
    # Die Regel dahinter, und sie ist der ganze Sinn: **ungefragt nein,
    # befohlen ja.** Was Ramzi ausdrücklich sagt ("mach Spotify auf"), darf ihn
    # auch aus dem Vollbild holen -- er hat ja darum gebeten. Gebunden wird
    # meine eigene Initiative, nicht sein Befehl.
    'haende': True,

    # Das Aufnahme-Fenster während der Aufnahme gar nicht zeigen. Ramzi hält
    # das selbst für eine Einstellung, die niemand braucht -- sie ist drin,
    # weil sie fast nichts kostet und in einem verkauften Produkt fehlen würde.
    'bild_fenster_aus': False,

    # Nach einem Fehlalarm von allein weiterreden.
    #
    # Die Lage, die es behebt (Ramzis Vorschlag vom 08.08.2026): mein eigener
    # Lautsprecher landet im Mikrofon, das Ohr hält das für seine Übernahme und
    # bricht meinen Satz ab. Erst die Brücke erkennt Sätze später als mein Echo
    # und verwirft sie -- richtig, aber danach stehe ich stumm da, und er muss
    # mich von Hand mit "rede weiter" anstoßen. In genau diesem Moment steht
    # fest, dass der Stopp ein Fehlalarm war; also hole ich den Rest selbst.
    #
    # Vorgabe an: die Lage ist eindeutig, und stumm stehenzubleiben ist nie das
    # Gewünschte. Der Schalter ist trotzdem da, weil ein Automatismus, der
    # meinen Mund aufmacht, abstellbar sein muss.
    'echo_weiterreden': True,

    # Darf ich ueberhaupt reden?
    #
    # Ramzis Auftrag vom 08.08.2026, waehrend er nach unten zum Kochen ging:
    # "dann brauchst du auch gar nicht zu reden ... damit du nicht umsonst
    # redest." Ueber die Lautstaerke auf Null ging das schon; das bleibt auch
    # so. Er wollte es aber OBEN bei den Knoepfen mit einem Griff erreichen,
    # statt dafuer zum Regler zu scrollen.
    #
    # Der Unterschied zur Lautstaerke Null ist nicht nur der Weg: was hier
    # abgewiesen wird, steht als UNGESAGT im Protokoll und ist mit "nochmal"
    # nachzuhoeren. Er verpasst also nichts, waehrend er weg ist -- es wartet
    # nur, statt in ein leeres Zimmer gesprochen zu werden.
    'reden': True,

    # DIESE ZWEI GEHÖREN HIERHER, auch wenn nur die Tafel sie setzt -- und das
    # ist kein Schönheitsfehler, sondern ein stiller Datenverlust, der hier
    # lauerte:
    #
    # `setze()` baut die Datei aus `alle()` neu auf, und `alle()` behält nur,
    # was in dieser Tabelle steht. Ein Schlüssel, der in der Datei liegt, hier
    # aber fehlt, wird also bei JEDER gesprochenen Änderung weggeschrieben.
    # "Tempo eins vier" hätte gereicht, um Sichtdauer und Zoom zu löschen --
    # die Tafel wäre beim nächsten Start auf die Vorgaben zurückgesprungen,
    # ohne dass jemand etwas verstellt hat.
    #
    # Gefunden am 07.08.2026 beim Anschließen der Sprachreflexe an genau diese
    # beiden Werte. Sichtbar war der Fehler nie: zwischen einem gesprochenen
    # Befehl und dem nächsten Tafel-Neustart sieht selten jemand nach.
    'mindest_anzeige_sekunden': 5,   # Sichtdauer, Bereich "Sehen"
    'tafel_zoom': 100,               # Zoomstufe der Tafel in Prozent
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
