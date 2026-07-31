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
        lautstaerke.daempfen() if an else lautstaerke.zuruecksetzen()
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

        # Warten, bis niemand sonst spricht. Ohne das reden zwei Prozesse
        # gleichzeitig: das Ohr beantwortet einen Reflex, während der Stop-Hook
        # meine Chat-Antwort vorliest. Ramzi hat am 31.07.2026 beides
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

            # Ein Strom für alle Sätze: sonst knackt es bei jedem Satzwechsel.
            strom = sd.OutputStream(samplerate=rate, channels=1, dtype='int16')
            strom.start()
            try:
                for satz in saetze:
                    if self._stop.is_set():
                        break
                    for stueck in stimme.synthesize(satz, syn_config=klang):
                        if self._stop.is_set():
                            break
                        strom.write(stueck.audio_int16_array)
            finally:
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
