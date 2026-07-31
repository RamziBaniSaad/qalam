"""Sprachausgabe für Qalam — lokal, offline, ohne Netz und ohne Kosten.

Nutzt Piper (ONNX). Deutsche Stimmen liegen in `stimmen/`.

Warum satzweise gesprochen wird: Bei einem Gespräch zählt nicht, wann die
Antwort *fertig* ist, sondern wann der erste Ton kommt. Piper braucht für einen
Satz wenige Millisekunden — wird also schon gesprochen, während der Rest noch
entsteht, wartet der Zuhörer nie auf den ganzen Text.

Aufruf von außen (das ist der Weg, den Skripte nehmen):
    python -m src.voice_output "Hallo Ramzi."
    python -m src.voice_output --stimme de_DE-ramona-low "Hallo."
    echo "Text" | python -m src.voice_output -
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
        laut = max(0.0, min(1.5, float(einstellungen.hole('lautstaerke') or 1.0)))
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
SAETZE_PRO_ANZEIGE = 2
# Grobe Obergrenze fuer eine Anzeige, in Zeichen. Nur fuer die EINTEILUNG --
# die angezeigte Dauer kommt danach aus dem echten Ton. Etwa 120 Zeichen sind
# bei normalem Tempo gut vier Sekunden: lang genug zum Lesen, kurz genug, dass
# der Streifen keine Wand wird.
ZEICHEN_PRO_ANZEIGE = 120


def _anzeigen_einteilen(saetze):
    """Sätze zu Anzeigen bündeln -- Ramzis Hybrid aus Sätzen UND Länge.

    Sein Wunsch, wortgetreu: "normalerweise zwei Sätze, aber gleichzeitig
    abhängig von der Zeit -- wenn du einen langen Satz hast, der so viel Zeit
    braucht wie zwei, dann nimmst du nur den."

    Die ERSTE Anzeige ist absichtlich kürzer. Grund, nachgemessen: bevor der
    erste Ton läuft, muss die erste Anzeige fertig erzeugt sein -- nur so steht
    ihre Dauer fest. Bei voller Länge waren das 0,52 s bis zum ersten Wort statt
    der gewohnten 0,12 s, und eine Assistentin, die eine halbe Sekunde später
    anfängt, fühlt sich träger an. Ein einzelner erster Satz halbiert das,
    danach läuft die Erzeugung ohnehin dem Abspielen davon.
    """
    anzeigen, aktuell, zeichen = [], [], 0
    for satz in saetze:
        erste = not anzeigen
        grenze_saetze = 1 if erste else SAETZE_PRO_ANZEIGE
        grenze_zeichen = 60 if erste else ZEICHEN_PRO_ANZEIGE
        if aktuell and (len(aktuell) >= grenze_saetze
                        or zeichen + len(satz) > grenze_zeichen):
            anzeigen.append(' '.join(aktuell))
            aktuell, zeichen = [], 0
        aktuell.append(satz)
        zeichen += len(satz)
    if aktuell:
        anzeigen.append(' '.join(aktuell))
    return anzeigen


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
            stimme = self.stimme
            rate = stimme.config.sample_rate
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

            anzeigen = _anzeigen_einteilen(saetze)
            fertig = queue.Queue(maxsize=1)
            schluss = threading.Event()

            def _erzeuge():
                for stueck in anzeigen:
                    if schluss.is_set():
                        break
                    try:
                        teile = [c.audio_int16_array
                                 for c in stimme.synthesize(stueck, syn_config=klang)]
                        ton = np.concatenate(teile) if teile else None
                    except Exception:
                        ton = None
                    while not schluss.is_set():
                        try:
                            fertig.put((stueck, ton), timeout=0.2)
                            break
                        except queue.Full:
                            continue
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
                    _untertitel(text, _wortzeiten(text, dauer), time.time(), dauer)
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


def nach_wav(text, ziel, stimme=STANDARD_STIMME):
    """Text als WAV-Datei ablegen, statt ihn zu sprechen (zum Vergleichen)."""
    import wave
    s = _lade_stimme(stimme)
    klang = _klangvorgaben()      # dieselben Vorgaben wie beim Sprechen
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
    else:
        Sprecher(args.stimme).sprich(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
