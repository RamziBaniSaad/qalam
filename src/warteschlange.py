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


def ist_mein_echo(gehoert):
    """Ist das, was das Ohr gehört hat, in Wahrheit meine eigene Stimme?

    Verglichen wird über die Wörter, nicht Zeichen für Zeichen: das schnelle
    Modell verhört sich, und ein einziger falscher Buchstabe darf die Erkennung
    nicht kippen. Kurze Wörter fliegen raus, weil "und", "das", "ist" in jedem
    Satz vorkommen und sonst jede Äußerung wie mein Echo aussähe.
    """
    meiner = _mein_satz()
    if not meiner or not gehoert:
        return False
    g = _worte(gehoert)
    if not g:
        return False
    return len(g & _worte(meiner)) / len(g) >= 0.6


def ramzi_ist_dran():
    """Ist gerade sein Platz in der Warteschlange -- soll ich also warten?"""
    return qalam_nimmt_auf() or ramzi_redet()


def warte_bis_er_fertig_ist(hoechstens=20.0):
    """Blockiert, bis Ramzi seinen Platz in der Warteschlange abgegeben hat.

    Mit Obergrenze: hängt irgendwo ein Merker fest (Absturz, vergessenes
    Aufräumen), darf ich nicht für immer stumm bleiben. Lieber einmal
    dazwischenreden als nie wieder etwas sagen."""
    ende = time.time() + hoechstens
    while ramzi_ist_dran() and time.time() < ende:
        time.sleep(0.15)
