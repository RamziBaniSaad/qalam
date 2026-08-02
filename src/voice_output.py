"""Sprachausgabe für Qalam — lokal, offline, ohne Netz und ohne Kosten.

Nutzt Piper (ONNX). Deutsche Stimmen liegen in `stimmen/`.

Warum satzweise gesprochen wird: Bei einem Gespräch zählt nicht, wann die
Antwort *fertig* ist, sondern wann der erste Ton kommt. Piper braucht für einen
Satz wenige Millisekunden — wird also schon gesprochen, während der Rest noch
entsteht, wartet der Zuhörer nie auf den ganzen Text.

Aufruf von außen (das ist der Weg, den Skripte nehmen):
    python src/voice_output.py "Hallo Ramzi."
    python src/voice_output.py --stimme de_DE-ramona-low "Hallo."
    echo "Text" | python src/voice_output.py -

Als Datei aufrufen, nicht als `-m src.voice_output`: bei `-m` liegt der
Repo-Ordner im Suchpfad, nicht `src/` — und die Untertitel-Einteilung holt sich
`untertitel` als Nachbarn. Der Modul-Aufruf bricht deshalb erst mitten im
Sprechen ab, mit `ModuleNotFoundError: untertitel`.
"""
import contextlib
import os
import queue
import re
import sys
import threading
import time

# Piper und sounddevice erst bei Bedarf laden -- der Import kostet ~1 s, und
# nicht jeder Start von Qalam braucht die Stimme.
_voice_cache = {}

STIMMEN_ORDNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'stimmen')
STANDARD_STIMME = 'de_DE-thorsten-medium'

# Satzende erkennen: Punkt/Frage/Ausruf gefolgt von Leerzeichen, aber nicht bei
# Abkürzungen wie "z.B." oder Zahlen wie "3.000".
_SATZ_ENDE = re.compile(r'(?<=[.!?…])\s+(?=[A-ZÄÖÜ„"(])')


def _zusammengezogen(phoneme):
    """Zerlegte Zeichen zusammenziehen: 'c' + kombinierende Cedille -> 'ç'.

    DER ICH-LAUT. Gefunden am 01.08.2026, als beim Erzeugen von Stimmproben
    zwanzigmal "Missing phoneme from id map: ̧" im Protokoll stand.

    espeak-ng liefert den deutschen ich-Laut als ZWEI Zeichen (c + U+0327).
    Fünf der neun deutschen Piper-Stimmen -- eva_k, karlsson, kerstin, pavoque,
    ramona, alle aus der älteren "low"-Reihe -- kennen in ihrer Phonem-Karte nur
    die zusammengesetzte Form ç (U+00E7). Die Cedille fällt dort also heraus,
    und übrig bleibt ein nacktes "c": aus "ich" wird ein Laut, den es im
    Deutschen nicht gibt. Bei "ich", "nicht", "mich", "möchte" -- also in jedem
    zweiten Satz.

    Warum das hier steht und nicht in der Bibliothek: `synthesize()` ruft
    `self.phonemize()` auf, und diese Funktion wird beim Laden davorgesetzt.
    Damit gilt die Reparatur für JEDEN Weg (Sprechen, WAV schreiben, Probe-Knopf)
    und die Bibliothek bleibt unangetastet.

    Angefasst wird ausschliesslich ein kombinierendes Zeichen, und nur wenn es
    sich wirklich zusammenziehen lässt -- ein Phonem, das aus mehreren echten
    Zeichen besteht, bleibt unberührt."""
    import unicodedata
    aus = []
    for p in phoneme:
        if aus and len(p) == 1 and unicodedata.combining(p):
            zusammen = unicodedata.normalize('NFC', aus[-1] + p)
            if len(zusammen) == 1:
                aus[-1] = zusammen
                continue
        aus.append(p)
    return aus


def _braucht_reparatur(stimme):
    """Kennt das Modell nur die zusammengesetzte Cedille? Dann fehlt ihm der
    ich-Laut, sobald espeak ihn zerlegt liefert."""
    try:
        karte = stimme.config.phoneme_id_map or {}
    except Exception:
        return False
    return '̧' not in karte and 'ç' in karte


def _lade_stimme(name):
    """Stimme laden und merken. Das Laden kostet ~0,5 s, das Sprechen danach kaum."""
    if name in _voice_cache:
        return _voice_cache[name]

    from piper import PiperVoice
    pfad = os.path.join(STIMMEN_ORDNER, f'{name}.onnx')
    if not os.path.exists(pfad):
        raise FileNotFoundError(
            f'Stimme "{name}" fehlt. Holen mit:\n'
            f'  python -m piper.download_voices --download-dir stimmen {name}'
        )
    stimme = PiperVoice.load(pfad)
    if _braucht_reparatur(stimme):
        # Nur für die betroffenen Modelle. Die aktuelle Stimme
        # (thorsten-medium) kennt beide Schreibweisen und läuft unverändert
        # durch -- an einer Stimme, die funktioniert, wird nichts geändert.
        _urspruenglich = stimme.phonemize
        stimme.phonemize = lambda text: [_zusammengezogen(p)
                                         for p in _urspruenglich(text)]
    _voice_cache[name] = stimme
    return stimme


def in_saetze(text):
    """Text in Sätze zerlegen, damit satzweise gesprochen werden kann."""
    text = ' '.join(text.split())
    if not text:
        return []
    return [s for s in _SATZ_ENDE.split(text) if s.strip()]


def _leiser(an):
    """Musik dämpfen bzw. zurückstellen, ohne daran scheitern zu können."""
    try:
        import lautstaerke
        # Eigener Prozess, nicht eigener Faden -- Begruendung in lautstaerke.py:
        # dieser Code hat das Ohr schon einmal getoetet.
        (lautstaerke.daempfen_im_hintergrund() if an
         else lautstaerke.zuruecksetzen_im_hintergrund())
    except Exception:
        pass


_video_prozess = None


def _videos(an):
    """Videos anhalten bzw. wieder anlaufen lassen -- siehe videos.py.

    Ramzi am 02.08.2026: Dämpfen reicht bei einem Video nicht, das Bild läuft
    ja weiter. „Ich möchte davon eigentlich nichts verpassen." Musik bleibt
    ausdrücklich beim Dämpfen.

    Eigener PROZESS, kein Faden: die Medien-Schnittstelle kommt über COM/WinRT
    ins Haus, und genau diese Sorte Aufruf hat am 31.07. schon einmal das Ohr
    getötet (siehe lautstaerke.py). Anders als damals bei der Lautstärke ist
    hier nachgemessen, dass ein abgesetzter Prozess die Sitzungen auch wirklich
    sieht -- dort war genau das der Grund, warum es beim Faden bleiben musste.
    """
    global _video_prozess
    try:
        import subprocess
        skript = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'videos.py')
        p = subprocess.Popen(
            [sys.executable, skript, '--anhalten' if an else '--fortsetzen'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if an:
            _video_prozess = p
    except Exception:
        pass


def _videos_abwarten():
    """Warten, bis wirklich angehalten ist -- aber nicht ewig.

    Der Aufruf dauert gemessen ~415 ms. Die stehen NICHT vor dem ersten Wort
    herum: angestoßen wird er zusammen mit der Dämpfung, abgewartet erst kurz
    bevor der erste Ton rausgeht -- dazwischen erzeugt Piper ohnehin. Damit
    kostet das Anhalten in der Praxis fast nichts.

    Der Zeitausfall ist Absicht: hängt die Medien-Schnittstelle, rede ich
    lieber über ein laufendes Video, als gar nicht zu reden.
    """
    global _video_prozess
    p, _video_prozess = _video_prozess, None
    if p is None:
        return
    try:
        p.wait(timeout=3)
    except Exception:
        pass


def _notbremse_lautstaerke():
    """Zurückstellen, falls es sonst niemand tut.

    Normalerweise gehört das Zurückstellen dem Wächter im Assistenten -- der
    weiß, ob Ramzi noch im Gespräch ist, und nur einer darf entscheiden. Läuft
    das Ohr aber gar nicht (Qalam allein, oder es ist abgestürzt), gibt es
    diesen Wächter nicht, und die Musik bliebe für immer leise.

    Deshalb hier ein Netz statt einer zweiten Zuständigkeit: liegt der Merker
    schon länger als eine Minute, hat ihn niemand abgeholt."""
    try:
        import lautstaerke
        if not os.path.exists(lautstaerke.MERKER):
            return
        if time.time() - os.path.getmtime(lautstaerke.MERKER) > 60:
            lautstaerke.zuruecksetzen()
    except Exception:
        pass


def _klangvorgaben():
    """Tempo und Lautstärke aus den Einstellungen holen.

    `length_scale` ist die Länge, nicht die Geschwindigkeit -- also der
    Kehrwert. Tempo 1.25 heisst length_scale 0.8, und Piper spricht schneller.
    Bei jedem Satz neu gelesen, damit ein verschobener Regler sofort wirkt,
    ohne dass irgendetwas neu startet."""
    try:
        from piper import SynthesisConfig
        import einstellungen
        tempo = max(0.5, min(2.0, float(einstellungen.hole('tempo') or 1.0)))
        # Zwei Ebenen, wie überall beim Feedback (Ramzi, 01.08.2026 nachts):
        # `lautstaerke` ist der Hauptregler für alles Hörbare, `laut_stimme` der
        # Anteil davon, der auf meine Stimme entfällt. Wer die Tafel oben ganz
        # herunterzieht, macht damit auch mich still -- genau das war der Wunsch.
        haupt = max(0.0, min(1.5, float(einstellungen.hole('lautstaerke') or 1.0)))
        anteil = max(0.0, float(einstellungen.hole('laut_stimme') or 100)) / 100.0
        laut = max(0.0, min(1.5, haupt * anteil))
        return SynthesisConfig(length_scale=1.0 / tempo, volume=laut)
    except Exception:
        return None


@contextlib.contextmanager
def _redeplatz(wartezeit=25.0):
    """Nur einer redet -- über Prozessgrenzen hinweg.

    Das Problem ist nicht ein Programm, sondern zwei: das Ohr (assistant.py)
    beantwortet einen Reflex, und gleichzeitig liest der Stop-Hook aus einem
    ganz anderen Prozess meine Chat-Antwort vor. Ein Schloss innerhalb eines
    Programms hilft dagegen nicht.

    Ein benannter Windows-Mutex schon: den kennen alle Prozesse unter demselben
    Namen, und Windows gibt ihn von selbst frei, wenn ein Prozess abstürzt --
    eine Sperrdatei bliebe liegen und ich wäre für immer stumm.

    Auf anderen Systemen passiert hier nichts; dort läuft die Sprachschicht
    ohnehin nicht (siehe project_noor_windows_macos im Gedächtnis).
    """
    if sys.platform != 'win32':
        yield
        return

    import ctypes
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    griff = k32.CreateMutexW(None, False, 'Global\\NoorSprichtGerade')
    if not griff:
        yield
        return
    try:
        # Wartet, statt sofort loszureden. Läuft die Wartezeit ab, wird trotzdem
        # gesprochen -- lieber einmal übereinander als eine verschluckte Antwort.
        k32.WaitForSingleObject(ctypes.c_void_p(griff), int(wartezeit * 1000))
        yield
    finally:
        k32.ReleaseMutex(ctypes.c_void_p(griff))
        k32.CloseHandle(ctypes.c_void_p(griff))


# --- Untertitel für das, was ich selbst sage -------------------------------
#
# Ramzis Wunsch vom 01.08.2026: er will meine gesprochene Antwort mitlesen
# können, ohne im Chat danach zu suchen -- und sehen, welches Wort gerade
# klingt.
#
# Der springende Punkt: bei MIR ist das ein leichtes Problem, im Gegensatz zu
# seinen eigenen Untertiteln. Ich kenne den Text vorher, und ich erzeuge den Ton
# selbst -- ich muss die Dauer also nicht aus dem Tempo ausrechnen, ich ZÄHLE
# die erzeugten Samples. Damit ist auch seine Sorge erledigt, ein verschobener
# Tempo-Regler könnte die Anzeige aus dem Tritt bringen: das Tempo steckt bereits
# im fertigen Ton.
def _anzeigen_einteilen(text):
    """Sätze zu Anzeigen bündeln -- Ramzis Hybrid aus Sätzen UND Länge.

    Sein Wunsch, wortgetreu: "normalerweise zwei Sätze, aber gleichzeitig
    abhängig von der Zeit -- wenn du einen langen Satz hast, der so viel Zeit
    braucht wie zwei, dann nimmst du nur den."

    Die Regel selbst liegt seit dem 01.08.2026 in untertitel.py, weil sie beide
    Seiten betrifft -- meine Sätze und seine. Zwei Fassungen würden
    auseinanderlaufen, und dann sähen seine Untertitel wieder anders aus als
    meine; genau das wollte er ja loswerden.

    `erste_kuerzer` ist der einzige Unterschied und gilt nur hier: bevor der
    erste Ton läuft, muss die erste Anzeige fertig erzeugt sein -- nur so steht
    ihre Dauer fest. Nachgemessen sind das 0,46 s bis zum ersten Wort statt der
    gewohnten 0,12 s; ein kurzer erster Satz drückt das, so weit es geht.
    """
    import untertitel
    return untertitel.einteilen(text, erste_kuerzer=True)


def _wortzeiten(text, dauer):
    """Die gemessene Dauer auf die Wörter verteilen.

    Piper liefert keine echten Wortzeiten -- dafür bräuchte es eine
    Zwangsausrichtung, ein eigenes Modell und deutlich mehr Rechenzeit. Also
    wird nach Länge verteilt, mit einem Aufschlag für Satzzeichen, weil an einem
    Komma oder Punkt hörbar Zeit vergeht.

    Das ist eine Näherung, und ich sage das auch so: auf einem langen Satz kann
    die Hervorhebung ein paar Zehntel verrutschen. Weil jede Anzeige neu bei
    null anfängt, sammelt sich der Fehler aber nicht auf.
    """
    worte = [w for w in text.split() if w]
    if not worte or dauer <= 0:
        return [{'w': w, 'ab': 0.0, 'd': 0.0} for w in worte]

    gewichte = []
    for w in worte:
        g = len(w) + 1.0
        if w[-1] in ',;:':
            g += 2.0
        elif w[-1] in '.!?…':
            g += 3.5
        gewichte.append(g)

    gesamt = sum(gewichte)
    ergebnis, laufend = [], 0.0
    for w, g in zip(worte, gewichte):
        d = dauer * g / gesamt
        ergebnis.append({'w': w, 'ab': round(laufend, 3), 'd': round(d, 3)})
        laufend += d
    return ergebnis


def _untertitel(text, worte, start, dauer):
    """Auf den Streifen legen, ohne daran scheitern zu können.

    `dauer` ist der Grund, warum Ramzis Untertitel-Regler für MEINE Sätze nicht
    mehr gilt: der Streifen weiß damit, wie lange diese Anzeige zu hören ist,
    und blendet sie nicht mehr mitten im Satz aus.
    """
    try:
        import untertitel
        untertitel.zeige(text, 'noor', worte, start, dauer)
    except Exception:
        pass


def _er_hat_uebernommen():
    """Hat Ramzi den Platz in der Warteschlange genommen, WÄHREND ich rede?

    Beide Wege zählen, und dass der zweite jetzt dabei sein DARF, ist Ramzis
    Verdienst (01.08.2026). Er hat nicht lockergelassen: "während ich rede, ist
    das Sprechen blockiert -- da gibt's kein Abwürgen." Sein Bild war richtig,
    mein Einwand aber auch: das Ohr hört über sein Mikrofon meine eigene Stimme
    und hätte mich für ihn gehalten.

    Aufgelöst ist das jetzt im Ohr selbst: es vergibt den Platz nicht mehr
    blind, sondern vergleicht das Gehörte mit dem, was ich gerade sage --
    warteschlange.ist_mein_echo(). Damit bedeutet ramzi_redet() endlich das,
    was der Name sagt, und darf hier geprüft werden.

        Aufnahme-Sperre  -- er hat die Diktat-Taste gedrückt (sofort)
        ramzi_redet()    -- er spricht mich an, während ich rede (nach dem
                            nächsten Durchgang des Mitlauschers)
    """
    try:
        import warteschlange
        return warteschlange.ramzi_ist_dran()
    except Exception:
        return False


class Sprecher:
    """Spricht Text über die Lautsprecher. Ein Sprecher pro Prozess reicht.

    `sprich()` blockiert bis zum Ende. `sprich_im_hintergrund()` kehrt sofort
    zurück und lässt sich mit `stoppe()` unterbrechen -- das ist der Haken, an
    dem später das Unterbrechen-Dürfen (barge-in) hängt.
    """

    def __init__(self, stimme=STANDARD_STIMME):
        self.stimme_name = stimme
        self._stimme = None
        self._stop = threading.Event()
        self._thread = None

    @property
    def stimme(self):
        if self._stimme is None:
            self._stimme = _lade_stimme(self.stimme_name)
        return self._stimme

    def sprich(self, text):
        """Spricht den Text, Satz für Satz. Blockiert bis zum Ende."""
        import sounddevice as sd

        saetze = in_saetze(text)
        if not saetze:
            return

        # ERST sein Platz in der Warteschlange, DANN meiner.
        #
        # Ramzis Idee, wortgetreu (31.07.2026): "wenn ich gerade am Sprechen
        # bin, bin ich in der Warteschlange, und wenn du dann etwas sagen
        # willst, packst du es in die Warteschlange und bist als nächstes
        # dran." Ausgelöst dadurch, dass dieser Hook einmal genau mitten in
        # seiner Beschreibung angefangen hat, meine vorherige Antwort
        # vorzulesen -- beide Stimmen übereinander.
        try:
            import warteschlange
            warteschlange.warte_bis_er_fertig_ist()
        except Exception:
            pass

        # Warten, bis niemand sonst von MIR spricht. Ohne das reden zwei
        # Prozesse gleichzeitig: das Ohr beantwortet einen Reflex, während der
        # Stop-Hook meine Chat-Antwort vorliest. Ramzi hat am 31.07.2026 beides
        # übereinander gehört und nicht mehr auseinanderhalten können, was
        # Antwort auf was war.
        with _redeplatz():
            # Musik leiser, solange ICH rede -- nicht nur, solange Ramzi redet.
            #
            # Sein Einwand vom 31.07.2026, und er ist zwingend: "wenn du redest,
            # dann soll es auch runtergehen, sonst redest du mit der Musik und
            # dann höre ich gar nichts."
            #
            # Hier und nicht im Assistenten, weil hier JEDES Sprechen durchkommt:
            # die Reflexe des Ohrs und der Sprech-Hook, der meine Chat-Antwort
            # vorliest -- und der ist ein eigener Prozess. Deshalb liegt der
            # Merker der alten Lautstärken in einer Datei, siehe lautstaerke.py.
            _leiser(True)
            # Videos gleich mit anstoßen -- abgewartet wird erst kurz vor dem
            # ersten Ton, siehe _videos_abwarten().
            _videos(True)
            # Welcher Motor spricht: XTTS (Ludvig) oder Piper.
            #
            # XTTS klingt deutlich menschlicher -- Ramzi hat es am 02.08.2026
            # ueber vier Hoerrunden ausgewaehlt. Es hat aber zwei Bedingungen,
            # die Piper nicht hat: es braucht 2,6 GB auf der Karte, und es
            # halluziniert bei ungeschicktem Text (siehe stimme_xtts.py und
            # noor/werkzeuge/stimme-regeln.md).
            #
            # Deshalb ist Piper kein Auslaufmodell, sondern der Rueckfall: ist
            # die Karte voll (Spiel, viele Fenster) oder geht beim Erzeugen
            # etwas schief, spricht Piper SOFORT, statt dass ich stumm bleibe.
            # Auf der CPU wuerde XTTS elf Sekunden je Satz brauchen -- das ist
            # ausdruecklich kein Rueckfall, lieber eine schlichtere Stimme
            # sofort als die schoene eine halbe Minute spaeter.
            motor = 'piper'
            try:
                import einstellungen
                gewuenscht = (einstellungen.hole('stimme_motor') or 'xtts')
            except Exception:
                gewuenscht = 'xtts'
            xtts_rate = None
            if gewuenscht == 'xtts':
                try:
                    import stimme_xtts
                    xtts_rate = stimme_xtts.RATE
                    if stimme_xtts.bereit():
                        motor = 'xtts'
                    else:
                        # Laedt im Hintergrund; ab dem naechsten Satz ist es da.
                        stimme_xtts.vorwaermen()
                except Exception:
                    motor = 'piper'

            stimme = self.stimme
            rate = xtts_rate if motor == 'xtts' else stimme.config.sample_rate
            self._stop.clear()
            klang = _klangvorgaben()

            # Anzeigen bilden und EINE im Voraus erzeugen.
            #
            # Warum vorausgeschaut wird: um das gerade gesprochene Wort
            # hervorheben zu können, muss die Dauer einer Anzeige feststehen,
            # BEVOR der erste Ton davon läuft -- und die steht erst fest, wenn
            # der Ton erzeugt ist. Würde ich jede Anzeige erst dann erzeugen,
            # wenn die vorige zu Ende ist, entstünde zwischen je zwei Anzeigen
            # eine hörbare Lücke. Also erzeugt ein Faden die nächste, während
            # die aktuelle noch läuft; Piper ist um ein Vielfaches schneller als
            # Echtzeit, der Faden bleibt also mühelos vorn.
            #
            # Die Warteschlange fasst genau eins: mehr im Voraus zu erzeugen
            # brächte nichts und würde beim Abbrechen nur weggeworfen.
            import numpy as np

            anzeigen = _anzeigen_einteilen(' '.join(saetze))
            fertig = queue.Queue(maxsize=1)
            schluss = threading.Event()

            def _abliefern(stueck, ton):
                """Ein fertiges Paar in die Warteschlange, bis Platz ist."""
                while not schluss.is_set():
                    try:
                        fertig.put((stueck, ton), timeout=0.2)
                        return True
                    except queue.Full:
                        continue
                return False

            def _erzeuge_piper():
                for stueck in anzeigen:
                    if schluss.is_set():
                        break
                    try:
                        teile = [c.audio_int16_array
                                 for c in stimme.synthesize(stueck, syn_config=klang)]
                        ton = np.concatenate(teile) if teile else None
                    except Exception:
                        ton = None
                    if not _abliefern(stueck, ton):
                        break

            def _erzeuge():
                # XTTS bekommt den GANZEN Text in einem Aufruf -- Zerlegen ist
                # genau die Ursache des Kauderwelschs (gemessen, 240 Proben).
                # Die Anzeigen bekommen daraus Scheiben, damit die Untertitel
                # weiter mitlaufen. Geliefert wird stueckweise, deshalb faengt
                # der Ton nach ~0,7 s an statt nach 4-5 s.
                if motor == 'xtts':
                    etwas = False
                    try:
                        laut = getattr(klang, 'volume', 1.0) if klang else 1.0
                        for stueck, ton in stimme_xtts.stuecke(
                                ' '.join(saetze), tempo=stimme_xtts.TEMPO):
                            if schluss.is_set():
                                break
                            if laut != 1.0:
                                ton = (ton.astype(np.float32) * laut) \
                                    .clip(-32768, 32767).astype(np.int16)
                            etwas = True
                            if not _abliefern(stueck, ton):
                                break
                        _abliefern(None, None)
                        return
                    except Exception as e:
                        if etwas:
                            # Mittendrin abgebrochen: NICHT von vorn mit Piper
                            # anfangen -- Ramzi hoerte sonst den halben Satz
                            # zweimal, und die Abtastrate des offenen Stroms
                            # passt ohnehin nicht mehr.
                            print('[Stimme] XTTS brach mitten im Satz ab: %s' % e,
                                  flush=True)
                            _abliefern(None, None)
                            return
                        # Nicht stumm bleiben: Piper springt ein. Der Fehler
                        # gehoert ins Protokoll, sonst verschwindet ein
                        # kaputtes Modell lautlos hinter einer Stimme, die
                        # noch funktioniert.
                        print('[Stimme] XTTS faellt aus, Piper springt ein: %s' % e,
                              flush=True)
                _erzeuge_piper()
                # Das Schlusszeichen MUSS ankommen, sonst wartet der Abspieler
                # ewig auf eine Anzeige, die nie kommt. Also so lange anbieten,
                # bis Platz da ist -- ein einmaliger Versuch mit Zeitausfall hat
                # genau diesen Hänger erzeugt (gemessen: 62 s statt 18 s).
                while not schluss.is_set():
                    try:
                        fertig.put((None, None), timeout=0.2)
                        break
                    except queue.Full:
                        continue

            erzeuger = threading.Thread(target=_erzeuge, daemon=True)
            erzeuger.start()

            # JETZT muss das Video stehen -- der nächste Schritt macht Ton.
            _videos_abwarten()

            # Ein Strom für alle Sätze: sonst knackt es bei jedem Satzwechsel.
            strom = sd.OutputStream(samplerate=rate, channels=1, dtype='int16')
            strom.start()
            try:
                while True:
                    if self._stop.is_set():
                        break
                    # Und zwischen JEDEM Satz noch einmal nachsehen.
                    #
                    # Ramzi am 31.07.2026, nachdem ich mitten in seine Nachricht
                    # gesprochen habe: "das mit der Warteschlange funktioniert
                    # nicht so gut -- ich rede gerade und du hast trotzdem mit
                    # reingesprochen." Er hat recht, und die Lücke ist genau
                    # diese: die Prüfung oben lief EINMAL, bevor das erste Wort
                    # kam. Fängt er danach an, hört mich niemand mehr auf. Je
                    # länger ich rede, desto größer wird dieses Fenster -- und
                    # ich sollte an dem Abend länger reden, nicht kürzer.
                    #
                    # Der Satz ist die richtige Körnung: ich höre an einer
                    # natürlichen Stelle auf, nicht mitten im Wort.
                    if _er_hat_uebernommen():
                        break

                    text, ton = fertig.get()
                    if text is None:
                        break
                    if ton is None or len(ton) == 0:
                        continue

                    # Die Dauer wird GEMESSEN, nicht geschätzt: so viele Samples
                    # bei dieser Abtastrate sind genau so viele Sekunden.
                    dauer = len(ton) / float(rate)
                    if not text:
                        # Ein leerer Anzeigetext heißt: derselbe Untertitel
                        # bleibt stehen. XTTS liefert viele kleine Tonstücke zu
                        # EINER Anzeige -- die je Stück neu zu setzen würde den
                        # Streifen flackern lassen.
                        strom.write(ton)
                        continue
                    if motor == 'xtts':
                        # Keine Wort-Hervorhebung: sie wird aus der Zeichenzahl
                        # geschätzt, und XTTS dehnt und pausiert zu
                        # unterschiedlich, als dass das passte. Ramzi hat die
                        # falschen Sprünge sofort gesehen. Die geschätzte Dauer
                        # dient nur noch dazu, dass der Streifen nicht mitten
                        # im Satz ausblendet.
                        _untertitel(text, [], time.time(),
                                    len(text) * stimme_xtts.JE_ZEICHEN)
                    else:
                        _untertitel(text, _wortzeiten(text, dauer),
                                    time.time(), dauer)
                    strom.write(ton)
            finally:
                schluss.set()
                # Den Platz freimachen, damit der Erzeuger nicht ewig wartend
                # im Speicher hängt, wenn hier abgebrochen wurde.
                try:
                    fertig.get_nowait()
                except Exception:
                    pass
                strom.stop()
                strom.close()
                # Das Video wieder anlaufen lassen. Anders als bei der
                # Dämpfung gehört das HIER hin und nicht dem Wächter im
                # Assistenten: der weiß nichts davon, und ein stehendes Video
                # wäre schlimmer als leise Musik -- Ramzi säße vor einem Bild,
                # das nicht weitergeht. Fortgesetzt wird nur, was ich selbst
                # angehalten habe (videos.py).
                _videos(False)
                # Das Zurückstellen gehört dem Wächter im Assistenten -- hier
                # nur das Netz für den Fall, dass es den nicht gibt.
                _notbremse_lautstaerke()

    def sprich_im_hintergrund(self, text):
        """Startet das Sprechen und kehrt sofort zurück."""
        self.stoppe()
        self._thread = threading.Thread(target=self.sprich, args=(text,), daemon=True)
        self._thread.start()
        return self._thread

    def stoppe(self):
        """Bricht das Sprechen ab -- die Grundlage fürs Unterbrochenwerden."""
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._stop.clear()

    def spricht_gerade(self):
        return bool(self._thread and self._thread.is_alive())


def nach_wav(text, ziel, stimme=STANDARD_STIMME, sprecher=None, klang=None):
    """Text als WAV-Datei ablegen, statt ihn zu sprechen (zum Vergleichen).

    `sprecher` ist die Nummer bei Mehrsprecher-Modellen -- die Gefühls-Stimme
    hat acht (neutral, belustigt, geflüstert ...), das MLS-Modell 236. Ohne
    Angabe gilt die Vorgabe des Modells.

    `klang` überschreibt Tempo und Lautstärke. Das ist kein Beiwerk, sondern
    Voraussetzung für einen fairen Vergleich: ohne diesen Weg holt sich jede
    einzelne Probe die Werte FRISCH aus den Einstellungen -- verschiebt Ramzi
    währenddessen einen Regler, sind die Kandidaten unterschiedlich laut, und
    die lautere Stimme gewinnt jeden Blindtest. Am 01.08.2026 genau so passiert,
    während er die neuen Sprachbefehle ausprobierte."""
    import wave
    s = _lade_stimme(stimme)
    if klang is None:
        klang = _klangvorgaben()      # dieselben Vorgaben wie beim Sprechen
    if sprecher is not None and klang is not None:
        import dataclasses
        klang = dataclasses.replace(klang, speaker_id=sprecher)
    with wave.open(ziel, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(s.config.sample_rate)
        for satz in in_saetze(text) or [text]:
            for stueck in s.synthesize(satz, syn_config=klang):
                f.writeframes(stueck.audio_int16_bytes)
    return ziel


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description='Qalam-Sprachausgabe (Piper, lokal)')
    p.add_argument('text', nargs='?', default='-', help='Text, oder "-" für stdin')
    p.add_argument('--stimme', default=STANDARD_STIMME)
    p.add_argument('--nach-wav', default=None, help='statt sprechen: WAV schreiben')
    # Über eine Datei, weil Aufrufer ohne stdin existieren: ein losgelöst
    # gestarteter Prozess (Start-Process) hat keine Pipe, und der Text enthält
    # Anführungszeichen, Umlaute und Gedankenstriche, an denen die Kommandozeile
    # zwischen PowerShell, Windows und argparse zerbricht.
    p.add_argument('--datei', default=None, help='Text aus dieser UTF-8-Datei lesen')
    args = p.parse_args(argv)

    if args.datei:
        with open(args.datei, encoding='utf-8') as f:
            text = f.read()
    elif args.text == '-':
        text = sys.stdin.read()
    else:
        text = args.text
    text = text.strip()
    if not text:
        return 0

    if args.nach_wav:
        print(nach_wav(text, args.nach_wav, args.stimme))
        return 0

    # Schlafe ich? Dann kommt hier gar nichts raus.
    #
    # Ramzi am 01.08.2026: "jedes Mal, wenn du das Dashboard neu startest, du
    # schlaefst zwar, aber du sagst trotzdem, wie du dich gerade anhoerst. Dass
    # du dich kurz vorstellst, ist ja richtig -- aber das sollst du nur machen,
    # wenn du wach bist."
    #
    # Der Vorsteller ist der Probe-Knopf der Tafel, der beim Start einmal
    # ausloest. Die Sperre gehoert trotzdem NICHT dorthin, sondern hierher:
    # das hier ist die eine Tuer, durch die jeder fremde Sprecher geht -- die
    # Tafel, die Sprech-Hooks, meine eigenen Skripte. Eine Regel an der Tuer
    # ist eine Regel; dieselbe Regel in jedem Aufrufer ist eine Verabredung,
    # die der naechste Aufrufer nicht kennt.
    try:
        import warteschlange
        if not warteschlange.darf_sprechen():
            return 0
    except Exception:
        pass

    # Erst anstellen, dann reden -- auch von aussen.
    #
    # Ramzis Regel von Anfang an: wer redet, haelt den Platz. Der Assistent
    # haelt sich daran, dieser Weg hier bisher nicht -- und das sind genau die
    # Aufrufe, die aus dem Nichts kommen: der Probe-Knopf auf der Tafel, meine
    # eigenen Skripte. Am 01.08.2026 bin ich damit mitten in einen langen Satz
    # von ihm gefahren, den er nicht wiederholen wollte. Ein Aufruf, der nicht
    # wartet, ist kein Werkzeug, sondern ein Dazwischenreden.
    try:
        import warteschlange
        warteschlange.warte_bis_er_fertig_ist()
    except Exception:
        pass
    Sprecher(args.stimme).sprich(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
