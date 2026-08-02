"""Ein Briefkasten fuers Sprechen -- damit kurzlebige Prozesse nicht 16 s warten.

Das Problem, das erst mit XTTS entsteht: `noor-sprich.ps1` startet fuer jede
Zwischenmeldung einen NEUEN Prozess. Bei Piper war das egal, das Modell ist in
Millisekunden da. XTTS braucht 16 Sekunden zum Laden und 2,6 GB auf der Karte --
je Meldung neu zu laden waere absurd, und zwei Modelle gleichzeitig auf der
Karte wuerden Ramzis harte Grenze reissen.

Also spricht nur EINER: der laufende Assistent, der das Modell ohnehin warm
haelt. Alle anderen werfen ihren Satz hier ein und sind sofort fertig.

Warum Dateien und kein Netzwerk-Anschluss: ein Port muesste vergeben, belegt
und aufgeraeumt werden, und wenn der Assistent nicht laeuft, haengt der
Absender. Eine Datei ist da geduldiger -- sie liegt einfach, bis jemand kommt,
und der Absender kann in derselben Sekunde weiterarbeiten.

Geschrieben wird ueber eine Nebendatei und dann umbenannt: Umbenennen ist
unteilbar, deshalb liest der Assistent nie einen halben Satz.
"""
import os
import time

PROJEKT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KASTEN = os.path.join(PROJEKT, '.sprechpost')

# Aelter als das? Dann lag die Nachricht so lange, dass sie nichts mehr nuetzt
# -- z.B. weil der Assistent zwischendurch neu gestartet wurde. Ein
# Zwischenstand von vor zehn Minuten vorgelesen zu bekommen ist schlimmer als
# gar keiner.
HOECHSTALTER = 120.0


def einwerfen(text):
    """Satz in den Kasten legen. Gibt True, wenn er dort liegt."""
    text = (text or '').strip()
    if not text:
        return False
    try:
        os.makedirs(KASTEN, exist_ok=True)
        name = '%.6f.txt' % time.time()
        vor = os.path.join(KASTEN, name + '.teil')
        with open(vor, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(vor, os.path.join(KASTEN, name))
        return True
    except Exception:
        return False


def zustaendig():
    """Nimmt hier gerade jemand Post an?

    Der Assistent legt beim Start einen Merker an und raeumt ihn beim Beenden
    weg. Ohne ihn spricht der Absender lieber selbst -- lieber Piper aus dem
    eigenen Prozess als ein Satz, der in einem Ordner liegen bleibt.
    """
    try:
        merker = os.path.join(KASTEN, 'bereit')
        if not os.path.exists(merker):
            return False
        # Ein liegengebliebener Merker eines abgestuerzten Assistenten soll
        # nicht ewig Post anziehen: er wird laufend aufgefrischt.
        return time.time() - os.path.getmtime(merker) < 30.0
    except Exception:
        return False


def bereit_melden():
    try:
        os.makedirs(KASTEN, exist_ok=True)
        with open(os.path.join(KASTEN, 'bereit'), 'w') as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def abholen():
    """Aelteste Nachricht holen und entfernen. None, wenn nichts da ist."""
    try:
        if not os.path.isdir(KASTEN):
            return None
        namen = sorted(n for n in os.listdir(KASTEN) if n.endswith('.txt'))
        for n in namen:
            p = os.path.join(KASTEN, n)
            try:
                alt = time.time() - os.path.getmtime(p)
                with open(p, encoding='utf-8') as f:
                    text = f.read().strip()
                os.remove(p)
            except Exception:
                continue
            if alt > HOECHSTALTER or not text:
                continue
            return text
    except Exception:
        pass
    return None
