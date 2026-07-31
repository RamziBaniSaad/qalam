"""Musik leiser machen, solange gesprochen wird -- nicht ausschalten.

RAMZIS WUNSCH, WORTGETREU (31.07.2026): "nicht die Medien komplett ausmachen,
wenn ich rede, sondern wirklich nur komplett leiser, damit das im Hintergrund
noch läuft." Und der Teil, auf dem er ausdrücklich bestanden hat: hinterher
zurück auf **den Wert von vorher**, nicht auf 100 Prozent -- "damit, wenn ich
mal mit Absicht leiser gemacht habe, es auch wirklich dahin zurückgeht und nicht
auf einmal laut wird."

WARUM PRO PROGRAMM UND NICHT DIE HAUPTLAUTSTÄRKE:
    Die Hauptlautstärke würde meine eigene Stimme mit leiser machen -- Piper
    spricht über dasselbe Ausgabegerät. Windows kann die Lautstärke je Programm
    setzen (Audio-Sitzungen), und damit bleibt genau das laut, was laut bleiben
    soll.

    Gedämpft wird alles AUSSER den eigenen Prozessen. Das ist absichtlich
    breiter als "nur Spotify": Ramzi hört auch Videos im Browser, und beim Reden
    stört jeder Ton gleich viel -- das Mikrofon hört ihn mit und das Modell
    verhört sich daran.

Alleine ausprobieren:
    python src/lautstaerke.py
"""
import json
import os
import tempfile
import threading
import time

# Was NICHT gedämpft wird: meine eigene Stimme und die Sitzung, in der Ramzi
# mitliest. Kleingeschrieben, verglichen wird kleingeschrieben.
EIGENE = {'python.exe', 'pythonw.exe', 'claude.exe', 'piper.exe'}

# Auf welchen Anteil des VORHERIGEN Werts gedämpft wird. Nicht auf einen festen
# Wert: hätte Ramzi die Musik schon auf 30 Prozent, wären 15 Prozent absolut
# lauter als vorher.
#
# 15 und nicht 20: Ramzi hat bei 20 Prozent nachgehört und es war ihm "ein
# bisschen zu riskant" -- das Mikrofon soll die Musik nicht mithören.
ANTEIL = 0.15

# Was vorher wie laut war -- in einer DATEI, nicht im Speicher.
#
# Der Grund ist, dass zwei verschiedene Prozesse dämpfen: das Ohr (wenn Ramzi
# redet) und der Sprech-Hook (wenn ich antworte -- der läuft als eigener
# PowerShell-Aufruf). Läge der Merker im Speicher, würde der zweite Prozess den
# schon gedämpften Wert für "vorher" halten und die Musik danach auf 15 Prozent
# von 15 Prozent zurückstellen. Nach zwei Runden wäre sie unhörbar, und niemand
# wüsste warum. Dieselbe Bauart wie Untertitel und Brücke: eine Datei ist das
# einzige, was mehrere Prozesse ohne Verrenkung erreichen.
MERKER = os.path.join(tempfile.gettempdir(), 'noor-lautstaerke.json')

_schloss = threading.Lock()


def _lies_merker():
    try:
        with open(MERKER, encoding='utf-8') as f:
            return {int(k): float(v) for k, v in json.load(f).items()}
    except Exception:
        return {}


def _schreib_merker(d):
    try:
        if d:
            with open(MERKER, 'w', encoding='utf-8') as f:
                json.dump({str(k): v for k, v in d.items()}, f)
        else:
            os.remove(MERKER)
    except OSError:
        pass


def _sitzungen():
    """Alle Audio-Sitzungen -- oder eine leere Liste, wenn es nicht geht.

    COM muss in jedem Faden einzeln initialisiert werden, und das Ohr ruft von
    einem Hintergrundfaden aus. Ohne das Initialisieren kommt hier eine
    Ausnahme, die niemand erwartet."""
    try:
        import comtypes
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        from pycaw.pycaw import AudioUtilities
        return AudioUtilities.GetAllSessions()
    except Exception as e:
        print(f'[Lautstärke] nicht erreichbar: {e}')
        return []


def _regler(sitzung):
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL      # noqa: F401  (nur fuer die Doku)
    from pycaw.pycaw import ISimpleAudioVolume
    return cast(sitzung._ctl.QueryInterface(ISimpleAudioVolume),
                POINTER(ISimpleAudioVolume))


def daempfen(anteil=ANTEIL):
    """Alles Fremde leiser stellen und sich merken, wie laut es war.

    Rückgabe: wie viele Programme gedämpft wurden."""
    anzahl = 0
    with _schloss:
        gemerkt = _lies_merker()
        for s in _sitzungen():
            try:
                if not s.Process:
                    continue
                name = (s.Process.name() or '').lower()
                if name in EIGENE:
                    continue
                regler = _regler(s)
                jetzt = regler.GetMasterVolume()
                if jetzt <= 0.001:
                    continue          # ist schon stumm, nichts zu merken
                # Nur beim ERSTEN Dämpfen merken. Sonst würde ein zweiter Ruf
                # den schon gedämpften Wert als "vorher" festschreiben, und die
                # Musik käme nie wieder auf ihre Lautstärke zurück.
                gemerkt.setdefault(s.Process.pid, jetzt)
                regler.SetMasterVolume(gemerkt[s.Process.pid] * anteil, None)
                anzahl += 1
            except Exception:
                continue
        _schreib_merker(gemerkt)
    return anzahl


def daempfen_im_hintergrund(anteil=ANTEIL):
    """Dämpfen, ohne den Aufrufer aufzuhalten.

    Das Aufzählen der Audio-Sitzungen geht über COM und braucht ein paar
    hundert Millisekunden. Ramzi hat sofort gemerkt, dass die Reaktion dadurch
    länger dauerte -- kein Wunder: der Aufruf stand im Faden des Mitlauschers,
    und der sucht dort eigentlich nach dem Weckwort. Nichts, was Ramzi hört,
    darf hinter einer Lautstärke-Abfrage warten."""
    threading.Thread(target=daempfen, args=(anteil,), daemon=True).start()


def zuruecksetzen():
    """Auf die Lautstärke von vorher zurück -- nicht auf 100 Prozent."""
    anzahl = 0
    with _schloss:
        gemerkt = _lies_merker()
        if not gemerkt:
            return 0
        for s in _sitzungen():
            try:
                if not s.Process:
                    continue
                alt = gemerkt.get(s.Process.pid)
                if alt is None:
                    continue
                _regler(s).SetMasterVolume(alt, None)
                anzahl += 1
            except Exception:
                continue
        _schreib_merker({})
    return anzahl


def gedaempft():
    return bool(_lies_merker())


# --------------------------------------------------------------------------
if __name__ == '__main__':
    print('Dämpfe …', daempfen(), 'Programme')
    for name, wert in list(_gemerkt.items()):
        print(f'   Prozess {name}: war {wert:.2f}')
    time.sleep(4)
    print('Zurück …', zuruecksetzen(), 'Programme')
