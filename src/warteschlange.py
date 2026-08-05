"""Die Warteschlange fürs Reden -- wer redet gerade, dem redet niemand rein.

RAMZIS IDEE, WORTGETREU (31.07.2026): "es gibt eine Warteschlange für die
Sachen, die du sagst -- ich möchte, dass das Sprechen von mir selber auch zu
dieser Warteschlange gehört. Wenn ich gerade am Sprechen bin, bin ich gerade in
dieser Warteschlange, und wenn du dann etwas sagen möchtest, packst du es in
die Warteschlange und bist als nächstes dran."

Ausgelöst dadurch, dass der Stop-Hook mitten in seiner Beschreibung angefangen
hat, meine vorherige Antwort vorzulesen -- beide Stimmen übereinander.

Zwei Zustände, aus zwei ganz verschiedenen Quellen, die beide "Ramzi hat gerade
den Platz in der Warteschlange" bedeuten:

    Qalam nimmt ein Diktat auf           -- .aufnahme.lock (gab es schon)
    er ist mitten in einem Satz an mich  -- .ramzi-redet.lock (neu)

Absichtlich eine eigene, winzige Datei ohne schwere Importe (nur os, time):
gebraucht wird sie von JEDEM Prozess, der sprechen will -- auch vom Sprech-Hook,
der bei jeder Antwort neu startet und nicht erst numpy/sounddevice laden soll,
nur um nachzusehen, ob gerade Stille ist.
"""
import json
import os
import tempfile
import time

PROJEKT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUFNAHME_SPERRE = os.path.join(PROJEKT, '.aufnahme.lock')
REDET_SPERRE = os.path.join(PROJEKT, '.ramzi-redet.lock')
SCHLAF_MERKER = os.path.join(PROJEKT, '.schlaeft.lock')

# Sicherheitsnetze, keine normalen Ablaufwerte -- der normale Ablauf räumt
# explizit auf (siehe aufnahme_endet() und assistant.py::_geweckt()). Diese
# Zahlen greifen nur, wenn genau das mal ausbleibt (Absturz, Ausnahme).
AUFNAHME_ALTER = 900     # 15 Minuten, wie zuvor bei der reinen Aufnahme-Sperre
REDET_ALTER = 1.0        # kurz: wird alle 0,3 s erneuert, solange er redet


def qalam_nimmt_auf():
    """Nimmt Qalam gerade ein Diktat auf?"""
    if not os.path.exists(AUFNAHME_SPERRE):
        return False
    try:
        if time.time() - os.path.getmtime(AUFNAHME_SPERRE) > AUFNAHME_ALTER:
            return False
    except OSError:
        return False
    return True


def aufnahme_beginnt():
    try:
        with open(AUFNAHME_SPERRE, 'w') as f:
            f.write(str(time.time()))
    except OSError:
        pass


def aufnahme_endet():
    try:
        os.remove(AUFNAHME_SPERRE)
    except OSError:
        pass


# --- Schlafe ich gerade? ---------------------------------------------------
#
# RAMZIS AUFBAU (01.08.2026, sein Wort: "wie ein Tony-Stark-Setup"): bin ich
# wach, spreche ich IMMER -- Rueckfragen, Zusammenfassungen, egal ob sein
# Auftrag gesprochen, diktiert oder getippt kam. Schlafe ich, ist alles still:
# keine Frage wird vorgelesen, keine Zusammenfassung, und das Ohr hoert nur
# noch auf das Aufwachen.
#
# Damit haengt das Reden nicht mehr daran, WIE er mich erreicht hat, sondern
# nur noch an einem einzigen Schalter. Der muss deshalb dort liegen, wo ihn
# jeder sehen kann: die Sprech-Hooks sind eigene PowerShell-Prozesse und der
# Tafel-Sammler auch -- kein einziger von ihnen kann in den Speicher des
# Assistenten schauen, wo `schlaeft` bisher allein stand.
#
# Fehlt die Datei, bin ich WACH. Das ist die richtige Vorgabe: weiss niemand
# etwas, soll ich ansprechbar sein und nicht stumm. Aufgeraeumt wird beim
# Start des Assistenten -- ein frischer Start ist wach, damit ein Absturz im
# Schlaf mich nicht dauerhaft verstummen laesst.


def schlaeft():
    """Schlafe ich gerade? Für JEDEN Prozess lesbar."""
    return os.path.exists(SCHLAF_MERKER)


def schlaf_merken(an):
    """Einschlafen oder aufwachen -- geht durch wake_word.schlaeft (Property)."""
    try:
        if an:
            with open(SCHLAF_MERKER, 'w') as f:
                f.write(str(time.time()))
        else:
            os.remove(SCHLAF_MERKER)
    except OSError:
        pass


def darf_sprechen():
    """Darf ich jetzt den Mund aufmachen? Die eine Frage, die die Hooks stellen."""
    return not schlaeft()


def ramzi_redet():
    """Ist er gerade mitten in einem gesprochenen Satz an mich?"""
    try:
        return (time.time() - os.path.getmtime(REDET_SPERRE)) < REDET_ALTER
    except OSError:
        return False


def redet_merken(an):
    """Setzen oder löschen -- wird von wake_word.py und assistant.py gerufen.

    Gesetzt: alle 0,3 s neu, solange der Mitlauscher den Namen erkannt hat oder
    das Folgefenster offen ist (siehe wake_word.py::_mitlauscher).
    Gelöscht: sofort, sobald assistant.py::_geweckt() eine ECHTE Stille meldet
    (endgueltig=True) -- nicht erst, wenn REDET_ALTER abgelaufen ist. Sonst
    bliebe mein eigener Reflex bis zu eine Sekunde stumm, nachdem er
    aufgehört hat zu reden."""
    try:
        if an:
            with open(REDET_SPERRE, 'w') as f:
                f.write(str(time.time()))
        else:
            os.remove(REDET_SPERRE)
    except OSError:
        pass


# --- Mein eigenes Echo erkennen -------------------------------------------
#
# Das Problem, das Ramzi am 01.08.2026 nicht losgelassen hat: er redet, ich
# rede trotzdem weiter. Sein Bild dazu war richtig -- wer redet, hält den Platz,
# wie eine geöffnete Tabelle -- und mein Einwand hat ihn zu Recht gestört.
#
# Der Einwand stimmt trotzdem, und der Grund steht in wake_word.py: der Platz
# wird gesetzt, sobald der Name erkannt ist ODER das Folgefenster offen ist,
# und das ist 20 Sekunden lang der Fall. In genau diesen 20 Sekunden antworte
# ich. Sein Lautsprecher steht neben seinem Mikrofon, also hört das Ohr MICH,
# hält das für "da redet jemand" und erneuert den Platz. Prüfte ich den Platz
# während des Redens, würgte ich mich nach dem ersten Satz selbst ab.
#
# Die Lösung braucht aber keine Stimmerkennung, wie ich zuerst dachte, sondern
# nur etwas, das seit heute Nacht ohnehin da ist: ICH WEISS, WAS ICH GERADE
# SAGE. Der Untertitel-Streifen enthält genau den Satz samt Startzeit und
# gemessener Dauer. Was das Mikrofon hört, lässt sich also mit meinem eigenen
# Text vergleichen -- deckt es sich, war ich es selbst.
UNTERTITEL = os.path.join(tempfile.gettempdir(), 'noor-untertitel.json')


def _mein_satz():
    """Was ich GERADE sage -- oder None, wenn ich gerade nichts sage."""
    try:
        with open(UNTERTITEL, encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        return None
    if (d.get('wer') or '').lower() != 'noor':
        return None
    start, dauer = d.get('start'), d.get('dauer')
    if not start or not dauer:
        return None
    # Etwas Nachlauf: der Schall braucht seinen Weg, und das Ohr sieht immer
    # ein Stück Vergangenheit an.
    if not (start - 0.3 <= time.time() <= start + dauer + 1.0):
        return None
    return d.get('text') or None


def noor_spricht_gerade():
    return _mein_satz() is not None


def _worte(text):
    return {w for w in ''.join(
        c.lower() if c.isalnum() or c.isspace() else ' ' for c in text).split() if len(w) > 2}


def ist_mein_echo(gehoert, kurz_erlaubt=False):
    """Ist das, was das Ohr gehört hat, in Wahrheit meine eigene Stimme?

    Verglichen wird über die Wörter, nicht Zeichen für Zeichen: das schnelle
    Modell verhört sich, und ein einziger falscher Buchstabe darf die Erkennung
    nicht kippen. Kurze Wörter fliegen raus, weil "und", "das", "ist" in jedem
    Satz vorkommen und sonst jede Äußerung wie mein Echo aussähe.
    """
    if not gehoert:
        return False
    g = _worte(gehoert)
    if not g:
        return False

    meiner = _mein_satz()
    if meiner and len(g & _worte(meiner)) / len(g) >= 0.6:
        return True

    # Der Rückblick auf die letzte Minute -- Begründung in merke_gesagt().
    #
    # Erst ab fünf Wörtern, und das ist die Sicherung, auf die es ankommt:
    # ein kurzer Zuruf wie „Noor, stopp" oder „ja, mach" muss IMMER
    # durchkommen, auch wenn ich dieselben Wörter gerade selbst gesagt habe.
    # Ramzi wollte ausdrücklich weiter unterbrechen können -- lieber einmal
    # mein eigenes Echo bearbeiten als ihn überhören.
    # `kurz_erlaubt` hebt diese Sperre auf, und zwar genau dort, wo ich SELBST
    # gerade spreche (wake_word._mitlauscher).
    #
    # Ramzis Befund vom 03.08.2026, den ich vorher nicht gesehen hatte: beim
    # Vorlesen bricht mein eigener Lautsprecher meinen Satz ab. Der Mitlauscher
    # hört alle paar Sekunden einen kurzen Fetzen -- zwei, drei Wörter -- und
    # der fällt durch beide Netze: `_mein_satz()` hält nur das gerade laufende
    # Stück (der Schall hinkt hinterher, es passt oft nicht), und der Rückblick
    # unten greift erst ab fünf Wörtern. Also galt mein eigenes Echo als "Ramzi
    # hat übernommen", und ich habe mich selbst gestoppt.
    #
    # Während ich rede, ist meine Stimme nachweislich im Raum. Ein
    # Wortvergleich, der dann anschlägt, ist viel wahrscheinlicher Echo als
    # Zufall -- deshalb darf die Längensperre dort fallen. Sie bleibt überall
    # sonst: ein kurzer Zuruf muss durchkommen.
    if len(g) < 5 and not kurz_erlaubt:
        return False
    frueher = _zuletzt_gesagt()
    return bool(frueher) and len(g & _worte(frueher)) / len(g) >= 0.6


GESAGT = os.path.join(PROJEKT, '.noor-gesagt.json')
GESAGT_ALTER = 60.0      # so lange kann ein Satz von mir noch zurückkommen

# Dasselbe noch einmal zum LESEN. Die Datei oben ist eine Arbeitsdatei: sie
# hält nur die letzte Minute und sieht aus wie Maschinentext.
#
# Ramzis Auftrag vom 03.08.2026: was ich zwischendurch sage, steht nirgends zum
# Nachlesen -- die Zusammenfassung am Ende landet im Chat, die Zwischenrufe nur
# in der Luft und im Untertitel. Hat er gerade weggeschaut, ist es weg. Also
# hier, und aufmachen kann er es mit „mach mir auf, was du gesagt hast".
GESAGT_LOG = os.path.join(PROJEKT, 'noor-gesagt.log')


def merke_gesagt(text):
    """Aufschreiben, was ich gerade gesagt habe -- für den Echo-Vergleich.

    WARUM ES NICHT REICHT, NUR AUF DEN LAUFENDEN SATZ ZU SCHAUEN -- der Fehler
    vom 03.08.2026, der mir meine eigene Antwort als Auftrag zurückgeschickt
    hat:

    Das Ohr wertet einen ganzen Block aus, wenn Ramzi eine Pause macht -- im
    Fall des Fehlers waren das 14,1 Sekunden am Stück. Ausgewertet wird also
    NACH dem Sprechen, nicht währenddessen. `_mein_satz()` weiß aber nur, was
    ich GERADE sage; eine Sekunde später gibt es None zurück, und mein eigener
    Satz sah aus wie seiner.

    Dazu kam ein zweiter Grund: Piper spricht in Stücken, und der Untertitel
    hält immer nur das laufende Stück. Selbst im richtigen Moment hätte der
    Vergleich also nur einen Bruchteil dessen gesehen, was zu hören war -- zu
    wenig für die Sechzig-Prozent-Schwelle.

    Also wird hier mitgeschrieben, was in der letzten Minute aus mir kam. Eine
    Datei und keine Variable im Speicher, weil Sprechen und Hören zwei
    getrennte Prozesse sind.
    """
    if not text:
        return
    jetzt = time.time()
    try:
        with open(GESAGT, encoding='utf-8') as f:
            alt = json.load(f)
    except Exception:
        alt = []
    neu = [e for e in alt if jetzt - e.get('t', 0) < GESAGT_ALTER]
    neu.append({'t': jetzt, 'text': text})
    try:
        # Über eine Zwischendatei, damit der lesende Prozess nie eine halb
        # geschriebene Datei erwischt -- dort würde json still scheitern und
        # der Echo-Schutz wäre lautlos aus.
        h, tmp = tempfile.mkstemp(dir=PROJEKT, suffix='.json')
        with os.fdopen(h, 'w', encoding='utf-8') as f:
            json.dump(neu[-40:], f, ensure_ascii=False)
        os.replace(tmp, GESAGT)
    except Exception:
        pass
    # Zum Nachlesen, anhängend und mit Uhrzeit. Darf nie der Grund sein, warum
    # ein Satz nicht gesprochen wird -- deshalb sein eigenes try.
    try:
        with open(GESAGT_LOG, 'a', encoding='utf-8') as f:
            f.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")}  {text}\n')
    except OSError:
        pass


def _zuletzt_gesagt():
    """Alles, was in der letzten Minute aus mir kam, als ein Text."""
    try:
        with open(GESAGT, encoding='utf-8') as f:
            alt = json.load(f)
    except Exception:
        return ''
    jetzt = time.time()
    return ' '.join(e.get('text', '') for e in alt
                    if jetzt - e.get('t', 0) < GESAGT_ALTER)


def ramzi_ist_dran():
    """Ist gerade sein Platz in der Warteschlange -- soll ich also warten?"""
    return qalam_nimmt_auf() or ramzi_redet()


def warte_bis_er_fertig_ist(hoechstens=20.0):
    """Blockiert, bis Ramzi seinen Platz in der Warteschlange abgegeben hat.

    Mit Obergrenze: hängt irgendwo ein Merker fest (Absturz, vergessenes
    Aufräumen), darf ich nicht für immer stumm bleiben. Lieber einmal
    dazwischenreden als nie wieder etwas sagen."""
    ende = time.time() + hoechstens
    begonnen = time.time()
    musste_warten = ramzi_ist_dran()
    while ramzi_ist_dran() and time.time() < ende:
        time.sleep(0.15)

    # MESSEN, BEVOR GESCHRAUBT WIRD. Ramzis Befund vom 05.08.2026: rede ich,
    # während er redet, halte ich richtigerweise an -- hole den Satz danach aber
    # nicht nach. Der Verdacht ist genau diese Obergrenze: seine Diktate laufen
    # oft 30 bis 60 s, gewartet wird höchstens 20. Dann redet der Aufrufer
    # trotzdem los, also mitten in seinen Satz hinein, oder der Satz ist
    # inhaltlich längst überholt.
    #
    # Ob das wirklich der Grund ist, sagt erst diese Zeile im Protokoll. Die
    # Zahl einfach hochzusetzen wäre geraten -- und die Grenze steht aus einem
    # guten Grund hier: hängt ein Merker fest, wäre ich sonst dauerhaft stumm.
    if musste_warten:
        gewartet = time.time() - begonnen
        abgelaufen = ramzi_ist_dran()
        print(f'[Warteschlange] {gewartet:.1f}s gewartet, '
              + ('ABGELAUFEN -- rede trotzdem' if abgelaufen else 'er war fertig'),
              flush=True)
