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
import os
import time

PROJEKT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUFNAHME_SPERRE = os.path.join(PROJEKT, '.aufnahme.lock')
REDET_SPERRE = os.path.join(PROJEKT, '.ramzi-redet.lock')

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
