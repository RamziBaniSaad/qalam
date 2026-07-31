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

        stimme = self.stimme
        rate = stimme.config.sample_rate
        self._stop.clear()

        # Ein Strom für alle Sätze: sonst knackt es bei jedem Satzwechsel.
        strom = sd.OutputStream(samplerate=rate, channels=1, dtype='int16')
        strom.start()
        try:
            for satz in saetze:
                if self._stop.is_set():
                    break
                for stueck in stimme.synthesize(satz):
                    if self._stop.is_set():
                        break
                    strom.write(stueck.audio_int16_array)
        finally:
            strom.stop()
            strom.close()

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
    with wave.open(ziel, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(s.config.sample_rate)
        for satz in in_saetze(text) or [text]:
            for stueck in s.synthesize(satz):
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
