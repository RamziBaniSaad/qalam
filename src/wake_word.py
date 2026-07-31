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

    def __init__(self, beim_wecken, modell='small', geraet=None,
                 aggressivitaet=2, max_sekunden=25.0, stille_ms=None,
                 beim_erkennen=None, beim_mitschreiben=None, flink_modell='base'):
        self.beim_wecken = beim_wecken
        # Wird gerufen, SOBALD der Name im laufenden Satz auftaucht -- lange
        # bevor der Satz fertig ist. Dafür ist der Ton da.
        self.beim_erkennen = beim_erkennen
        # Wird mit dem vorläufigen Text gerufen, während noch gesprochen wird.
        self.beim_mitschreiben = beim_mitschreiben

        self.modell_name = modell
        self.flink_name = flink_modell
        self.geraet = geraet
        self.vad = webrtcvad.Vad(aggressivitaet)
        self.max_frames = int(max_sekunden * 1000 / FRAME_MS)
        self._stille_ms = stille_ms
        self._modell = None
        self._flink = None
        self._stop = threading.Event()
        self._thread = None
        self.schlaeft = False   # per "Noor, schlaf" abschaltbar
        # Bis wann ein Satz OHNE Weckwort noch als Auftrag gilt. Ramzi sagt oft
        # erst nur den Namen, wartet auf das Zeichen und redet dann weiter --
        # dieser zweite Satz enthält den Namen naturgemäß nicht mehr.
        self.folge_bis = 0.0

    @property
    def stille_frames(self):
        """Wie lange Schweigen einen Satz beendet -- aus den Einstellungen.

        Stand bis 31.07.2026 fest auf 600 ms. Ramzi konnte damit keinen Satz zu
        Ende sprechen: jede Denkpause hat mitten im Satz abgeschickt. Jetzt
        einstellbar und deutlich länger."""
        ms = self._stille_ms
        if ms is None:
            try:
                from einstellungen import hole
                ms = hole('stille_ms')
            except Exception:
                ms = 1600
        return int(ms / FRAME_MS)

    @property
    def modell(self):
        """Das genaue Modell für den fertigen Satz. Bewusst auf der CPU:
        int8, kein VRAM -- die Karte bleibt fürs Diktat und fürs Zocken frei."""
        if self._modell is None:
            from faster_whisper import WhisperModel
            self._modell = WhisperModel(self.modell_name, device='cpu', compute_type='int8')
        return self._modell

    @property
    def flink(self):
        """Das schnelle Modell für den Blick MITTENDRIN.

        Der Zielkonflikt, den es auflöst: eine lange Redepause braucht eine
        lange Stille-Schwelle -- aber dann käme das Zeichen "ich höre dich"
        erst zwei Sekunden nachdem Ramzi ausgeredet hat, und bis dahin redet er
        ins Ungewisse.

        Also wird schon WÄHREND des Sprechens mitgehört, mit dem kleinsten
        Modell und nur auf den letzten Sekunden. Das reicht völlig, um den
        Namen zu finden und einen vorläufigen Untertitel zu zeigen. Der genaue
        Satz kommt danach vom großen Modell."""
        if self._flink is None:
            from faster_whisper import WhisperModel
            self._flink = WhisperModel(self.flink_name, device='cpu', compute_type='int8')
        return self._flink

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
        _ = self.flink   # das schnelle zuerst -- es wird als Erstes gebraucht
        _ = self.modell
        puffer = collections.deque()
        stille = 0
        in_sprache = False
        erkannt = False          # Name im laufenden Satz schon gefunden?
        letzter_blick = 0.0

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

                    # Mittendrin hineinhören: alle paar Zehntel, sobald genug
                    # Ton da ist. Nur mit dem schnellen Modell und nur auf den
                    # letzten Sekunden -- das kostet wenig und bringt das
                    # Zeichen "ich höre dich" um Sekunden nach vorn.
                    jetzt = time.time()
                    if (len(puffer) >= 25 and jetzt - letzter_blick > 0.7
                            and not self.schlaeft):
                        letzter_blick = jetzt
                        vorlaeufig = self._hoer_kurz(puffer)
                        if vorlaeufig:
                            if not erkannt and WECKWORT.search(vorlaeufig):
                                erkannt = True
                                self._melde(self.beim_erkennen)
                            if erkannt or time.time() < self.folge_bis:
                                self._melde(self.beim_mitschreiben, vorlaeufig)

                    if len(puffer) >= self.max_frames:
                        self._pruefe(puffer)
                        puffer.clear()
                        in_sprache = False
                        erkannt = False
                elif in_sprache:
                    stille += 1
                    puffer.append(frame)
                    if stille >= self.stille_frames:
                        self._pruefe(puffer)
                        puffer.clear()
                        in_sprache = False
                        erkannt = False
                        stille = 0

    def _melde(self, rueckruf, *args):
        """Rückruf aufrufen, ohne dass ein Fehler darin das Ohr umbringt."""
        if not rueckruf:
            return
        try:
            rueckruf(*args)
        except Exception as e:
            print(f'[Weckwort] Rückruf fehlgeschlagen: {e}')

    def _hoer_kurz(self, puffer, sekunden=3.0):
        """Die letzten Sekunden mit dem schnellen Modell mithören.

        Nur ein Ausschnitt, nicht der ganze Puffer: die Rechenzeit soll gleich
        bleiben, egal wie lange Ramzi schon spricht."""
        frames = list(puffer)[-int(sekunden * 1000 / FRAME_MS):]
        if len(frames) < 20:
            return None
        audio = np.concatenate(frames).astype(np.float32) / 32768.0
        try:
            segmente, _ = self.flink.transcribe(audio, language='de', beam_size=1)
            return ' '.join(s.text for s in segmente).strip()
        except Exception:
            return None

    def _pruefe(self, puffer):
        """Fertiges Segment genau transkribieren und entscheiden."""
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

        # Der Folgesatz braucht den Namen nicht mehr.
        #
        # Ramzi sagt oft erst nur "Noor", wartet auf das Zeichen und redet dann
        # weiter. Dieser zweite Satz enthält den Namen naturgemäß nicht -- ohne
        # diese Regel wäre er verloren, und genau das hat sich für ihn angefühlt
        # wie "sie hört mir nicht mehr zu".
        im_gespraech = time.time() < self.folge_bis
        if WECKWORT.search(text) or im_gespraech:
            self._melde(self.beim_wecken, text)


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
