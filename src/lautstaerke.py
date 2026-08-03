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
    """Der Lautstärkeregler einer Audio-Sitzung.

    KEIN ctypes.cast auf POINTER(...) hier. Genau das hat am 31.07.2026 das Ohr
    getötet: `QueryInterface` gibt bereits einen fertigen, verwalteten
    Schnittstellenzeiger zurück. Ihn noch einmal zu casten erzeugt einen
    zweiten, unverwalteten Zeiger auf dasselbe Objekt -- und wenn der
    Speicherbereiniger den freigibt, kommt

        ValueError: COM method call without VTable
        OSError: exception: access violation writing 0x...

    Eine Zugriffsverletzung beendet den ganzen Prozess, und kein try/except
    fängt sie ab. Ramzi hat mehrfach meinen Namen gerufen und nichts bekam --
    das Ohr war schon tot, und die Tafel hat es korrekt als "Weckwort aus"
    gemeldet.

    Merksatz: bei COM ist ein zusätzlicher cast kein Schönheitsfehler, sondern
    ein Absturz mit Zeitzünder."""
    from pycaw.pycaw import ISimpleAudioVolume
    return sitzung._ctl.QueryInterface(ISimpleAudioVolume)


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
                if s.Process.pid in gemerkt:
                    # Steht schon im Merker, ist also schon gedämpft. Noch
                    # einmal zu setzen bringt nichts und wäre nur eine weitere
                    # Gelegenheit, etwas falsch zu machen.
                    continue
                if jetzt <= anteil + 0.02:
                    # Schon so leise, dass Dämpfen nichts brächte -- und wenn
                    # hier der Rest eines misslungenen Zurückstellens läge,
                    # würde ich ihn als "so war es vorher" festschreiben.
                    #
                    # GENAU SO ist Ramzis Chrome am 03.08.2026 auf 0,023
                    # gelandet: der Merker sagte "vorher 0,15", tatsächlich war
                    # 0,15 schon der gedämpfte Wert. 0,15 von 0,15. Er hatte
                    # den Verdacht von selbst -- "dann machst du von diesen 15
                    # Prozent nochmal die 15 Prozent" -- und er stimmte.
                    continue
                gemerkt[s.Process.pid] = jetzt
                regler.SetMasterVolume(jetzt * anteil, None)
                anzahl += 1
            except Exception:
                continue
        _schreib_merker(gemerkt)
    return anzahl


def _nebenher(schalter):
    """Die Lautstärke in einem eigenen FADEN verstellen.

    Warum überhaupt nebenher: das Aufzählen der Audio-Sitzungen geht über COM
    und braucht ein paar hundert Millisekunden. Ramzi hat sofort gemerkt, dass
    die Reaktion dadurch länger dauerte -- der Aufruf stand im Faden des
    Mitlauschers, und der sucht dort eigentlich nach dem Weckwort.

    WAS HIER EIGENTLICH STEHEN SOLLTE: ein eigener PROZESS. Am 31.07.2026 hat
    genau dieser Code das Ohr getötet -- ein falscher COM-Zeiger, eine
    Zugriffsverletzung, und die beendet den ganzen Prozess; kein try/except
    fängt sie ab. Ramzi rief mehrfach meinen Namen und bekam nichts, weil das
    Ohr schon tot war. Ein Nebenwunsch ("Musik leiser") darf das Ohr nicht
    umbringen können, auch nicht beim nächsten Fehler, den ich noch nicht kenne.

    Der Versuch mit einem eigenen Prozess ist gescheitert, und zwar unerklärt:
    derselbe Befehl, von Hand ausgeführt, dämpft einwandfrei -- als abgesetzter
    Prozess findet er keine einzige Audio-Sitzung. Statt das weiter auszufechten
    steht hier ein Faden, und die Absicherung liegt in der Korrektheit: der
    COM-Fehler ist behoben und über 35 Runden mit erzwungener Speicherbereinigung
    geprüft, auch aus mehreren Fäden. Der Prozess-Ansatz bleibt als offener Punkt
    notiert -- er wäre die bessere Trennung."""
    ziel = daempfen if schalter == 'daempfen' else zuruecksetzen
    threading.Thread(target=ziel, daemon=True).start()


def daempfen_im_hintergrund(anteil=ANTEIL):
    """Dämpfen, ohne den Aufrufer aufzuhalten und ohne ihn zu gefährden."""
    _nebenher('daempfen')


def zuruecksetzen_im_hintergrund():
    _nebenher('zuruecksetzen')


def zuruecksetzen():
    """Auf die Lautstärke von vorher zurück -- nicht auf 100 Prozent.

    VERGESSEN WIRD NUR, WAS AUCH WIRKLICH ZURÜCKGESTELLT WURDE. Vorher wurde
    der Merker am Ende bedingungslos geleert -- auch wenn `_sitzungen()` gerade
    nichts geliefert hat (COM zickt gelegentlich) oder das Programm im Moment
    nicht in der Liste stand. Dann blieb die Lautstärke unten, aber niemand
    wusste mehr, worauf sie gehört. Beim nächsten Dämpfen galt der leise Wert
    als "vorher", und es wurde noch leiser. Das ist der Fehler, an dem Ramzi am
    03.08.2026 sein Video kaum noch hören konnte.
    """
    anzahl = 0
    with _schloss:
        gemerkt = _lies_merker()
        if not gemerkt:
            return 0
        offen = dict(gemerkt)
        for s in _sitzungen():
            try:
                if not s.Process:
                    continue
                alt = gemerkt.get(s.Process.pid)
                if alt is None:
                    continue
                _regler(s).SetMasterVolume(alt, None)
                offen.pop(s.Process.pid, None)
                anzahl += 1
            except Exception:
                continue
        _schreib_merker(offen)
    return anzahl


def alles_laut():
    """Jedes Programm auf volle Lautstärke -- Ramzis Notausgang.

    Sein Auftrag vom 03.08.2026: „Statt dass ich in die Einstellungen gehe, in
    den Sound, und bei allen meinen Apps alles auf 100 Prozent mache, sage ich
    das ganz kurz, du machst das alles auf 100, und ich kann weitermachen."

    Bewusst ohne Merker und ohne Rücksicht auf das, was vorher war: das hier
    ist kein Zurückstellen, sondern die Handbremse für den Fall, dass beim
    Zurückstellen etwas schiefging. Ein Notausgang, der selbst wieder von einer
    Datei abhinge, wäre keiner.
    """
    anzahl = 0
    with _schloss:
        for s in _sitzungen():
            try:
                if not s.Process:
                    continue
                _regler(s).SetMasterVolume(1.0, None)
                anzahl += 1
            except Exception:
                continue
        _schreib_merker({})
    return anzahl


def gedaempft():
    return bool(_lies_merker())


# --------------------------------------------------------------------------
if __name__ == '__main__':
    import sys
    if '--daempfen' in sys.argv:
        daempfen()
    elif '--zurueck' in sys.argv:
        zuruecksetzen()
    else:
        # Selbsttest: dämpfen, hinhören, zurückstellen.
        print('Dämpfe …', daempfen(), 'Programme')
        for pid, wert in _lies_merker().items():
            print(f'   Prozess {pid}: war {wert:.2f}')
        time.sleep(4)
        print('Zurück …', zuruecksetzen(), 'Programme')
