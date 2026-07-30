"""Weckwort — Qalam hört zu, ohne dass eine Taste gedrückt wird.

WARUM NICHT openWakeWord (Stand 30.07.2026):
    openWakeWord ist der übliche Weg, aber es bringt nur englische Modelle mit
    ("alexa", "hey jarvis"). Für "Noor" müsste erst ein eigenes Modell trainiert
    werden -- machbar, aber eine eigene Baustelle. Bis dahin geht es einfacher,
    weil wir alles Nötige schon im Haus haben:

        webrtcvad merkt, DASS jemand spricht   (CPU, ~0 Last, hat Qalam schon)
        faster-whisper hört, WAS gesagt wurde  (winziges Modell, auf der CPU)

    Whisper läuft nur an, wenn wirklich gesprochen wurde -- nicht dauernd. Das
    kostet kein VRAM (bewusst CPU, damit Ramzis 8-GB-Karte frei bleibt) und
    versteht Deutsch ab der ersten Sekunde, ohne Training.

    Wenn "Noor" später als echtes Weckwortmodell trainiert ist, kann es hier
    davorgehängt werden; der Rest bleibt wie er ist.
"""
import collections
import os
import re
import threading
import time

import numpy as np
import sounddevice as sd
import webrtcvad

RATE = 16000
FRAME_MS = 30
FRAME_LEN = int(RATE * FRAME_MS / 1000)

# Wie Whisper "Noor" verhören kann. Bewusst großzügig: ein verpasstes Weckwort
# ist ärgerlicher als ein gelegentlicher Fehlstart, den man einfach ignoriert.
WECKWORT = re.compile(
    r'\b(n[ouû]{1,3}r|nuhr|noah|nura|nur|noor|nour)\b',
    re.IGNORECASE
)

PROJEKT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPERRE = os.path.join(PROJEKT, '.aufnahme.lock')


# --------------------------------------------------------------------------
# Sperre: solange Qalam selbst aufnimmt, ist das Weckwort taub.
#
# Ramzi sagt meinen Namen ständig, während er mir etwas diktiert. Ohne diese
# Sperre würde ich mitten in seinem Diktat aufwachen -- genau das soll nicht
# passieren.
# --------------------------------------------------------------------------
def aufnahme_beginnt():
    try:
        with open(SPERRE, 'w') as f:
            f.write(str(time.time()))
    except OSError:
        pass


def aufnahme_endet():
    try:
        os.remove(SPERRE)
    except OSError:
        pass


def qalam_nimmt_auf():
    if not os.path.exists(SPERRE):
        return False
    # Sicherheitsnetz: bleibt die Sperre nach einem Absturz liegen, taut sie
    # nach 15 Minuten von selbst auf, statt das Weckwort dauerhaft zu töten.
    try:
        if time.time() - os.path.getmtime(SPERRE) > 900:
            aufnahme_endet()
            return False
    except OSError:
        return False
    return True


class Weckwort:
    """Hört dauerhaft mit und ruft `beim_wecken(text)`, wenn der Name fällt.

    `text` ist der ganze erkannte Satz, nicht nur das Weckwort -- damit
    "Noor, mach Spotify an" in einem Rutsch funktioniert und man nicht erst
    gerufen wird und dann nochmal reden muss.
    """

    def __init__(self, beim_wecken, modell='base', geraet=None,
                 aggressivitaet=2, max_sekunden=6.0, stille_ms=600):
        self.beim_wecken = beim_wecken
        self.modell_name = modell
        self.geraet = geraet
        self.vad = webrtcvad.Vad(aggressivitaet)
        self.max_frames = int(max_sekunden * 1000 / FRAME_MS)
        self.stille_frames = int(stille_ms / FRAME_MS)
        self._modell = None
        self._stop = threading.Event()
        self._thread = None
        self.schlaeft = False   # per "Noor, schlaf" abschaltbar

    @property
    def modell(self):
        """Kleines Whisper-Modell, bewusst auf der CPU.

        int8 auf der CPU: ~75 MB RAM, kein VRAM. Die Karte bleibt für das
        eigentliche Diktat und fürs Zocken frei."""
        if self._modell is None:
            from faster_whisper import WhisperModel
            self._modell = WhisperModel(self.modell_name, device='cpu', compute_type='int8')
        return self._modell

    # ----------------------------------------------------------------------
    def starte(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._schleife, daemon=True)
        self._thread.start()

    def stoppe(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def laeuft(self):
        return bool(self._thread and self._thread.is_alive())

    # ----------------------------------------------------------------------
    def _schleife(self):
        _ = self.modell  # einmal laden, bevor es losgeht
        puffer = collections.deque()
        stille = 0
        in_sprache = False

        with sd.InputStream(samplerate=RATE, channels=1, dtype='int16',
                            blocksize=FRAME_LEN, device=self.geraet) as strom:
            while not self._stop.is_set():
                block, _ueberlauf = strom.read(FRAME_LEN)
                frame = block[:, 0]

                # Solange Qalam aufnimmt: wirklich taub. Das ist der einzige
                # harte Riegel -- Ramzi diktiert gerade, da habe ich zu schweigen.
                #
                # "Schlafen" ist ausdruecklich NICHT hier: wer schlaeft, muss
                # trotzdem geweckt werden koennen. Genau daran ist es beim
                # ersten Test gescheitert -- ich habe "wach auf" nie gehoert,
                # weil ich an dieser Stelle schon abgebrochen habe.
                if qalam_nimmt_auf():
                    puffer.clear()
                    in_sprache = False
                    continue

                try:
                    ist_sprache = self.vad.is_speech(frame.tobytes(), RATE)
                except Exception:
                    continue

                if ist_sprache:
                    in_sprache = True
                    stille = 0
                    puffer.append(frame)
                    if len(puffer) >= self.max_frames:
                        self._pruefe(puffer)
                        puffer.clear()
                        in_sprache = False
                elif in_sprache:
                    stille += 1
                    puffer.append(frame)
                    if stille >= self.stille_frames:
                        self._pruefe(puffer)
                        puffer.clear()
                        in_sprache = False
                        stille = 0

    def _pruefe(self, puffer):
        """Segment transkribieren und auf das Weckwort prüfen."""
        if len(puffer) < 8:      # unter ~0,25 s ist es kein Wort, sondern ein Geräusch
            return
        audio = np.concatenate(list(puffer)).astype(np.float32) / 32768.0
        try:
            segmente, _ = self.modell.transcribe(audio, language='de', beam_size=1)
            text = ' '.join(s.text for s in segmente).strip()
        except Exception as e:
            print(f'[Weckwort] Erkennung fehlgeschlagen: {e}')
            return

        if not text:
            return
        if WECKWORT.search(text):
            try:
                self.beim_wecken(text)
            except Exception as e:
                print(f'[Weckwort] Rückruf fehlgeschlagen: {e}')


# --------------------------------------------------------------------------
if __name__ == '__main__':
    # Selbsttest: sag "Noor" in dein Mikrofon.
    def gerufen(text):
        print(f'>>> geweckt: {text!r}')

    w = Weckwort(gerufen)
    print('Höre zu. Sag "Noor ..." -- Strg+C beendet.')
    w.starte()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        w.stoppe()
