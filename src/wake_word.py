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
import queue
import re
import threading
import time

import numpy as np
import sounddevice as sd
import webrtcvad

RATE = 16000
FRAME_MS = 30
FRAME_LEN = int(RATE * FRAME_MS / 1000)
# Wie viel Ton der Mitlauscher jeweils ansieht: drei Sekunden reichen, um den
# Namen zu finden, und halten seine Rechenzeit konstant.
BLICK_FRAMES = int(3.0 * 1000 / FRAME_MS)

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
                 aggressivitaet=3, max_sekunden=15.0, stille_ms=None,
                 beim_erkennen=None, beim_mitschreiben=None, flink_modell='base',
                 spricht_gerade=None):
        self.beim_wecken = beim_wecken
        # Solange ICH spreche, ist das Ohr taub -- sonst hört das Mikrofon
        # meine eigene Stimme aus den Lautsprechern mit, das flinke Modell
        # verhört sich daran zu Zufallstext, und der landet als "Befehl" bei
        # mir selbst. Ramzi hat das am 31.07.2026 live erlebt: "du hörst,
        # was du geschrieben hast, und schickst das rüber". Derselbe
        # Mechanismus wie qalam_nimmt_auf(), nur für die eigene Stimme statt
        # für ein laufendes Diktat.
        self._spricht_gerade = spricht_gerade or (lambda: False)
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
        self._schloss = threading.Lock()
        self._laufend = None
        self._auftraege = queue.Queue()
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
        """Nur zuhören und einsammeln -- NIEMALS hier etwas ausrechnen.

        Der Fehler, der das am 31.07.2026 einmal komplett zerstört hat: Die
        Erkennung lief in genau dieser Schleife. Während transkribiert wurde,
        hat niemand mehr Ton vom Mikrofon abgeholt, der Eingangspuffer lief
        über, und Ramzi bekam nach zwanzig Sekunden alles auf einmal und
        verstümmelt. Er hat es sofort gemerkt: "das ist komplett kaputt".

        Merksatz: eine Aufnahmeschleife darf nur lesen. Alles, was Zeit kostet,
        gehört in einen eigenen Faden.
        """
        _ = self.flink   # das schnelle zuerst -- es wird als Erstes gebraucht
        _ = self.modell

        self._auftraege = queue.Queue()
        self._laufend = None
        threading.Thread(target=self._arbeiter, daemon=True).start()
        threading.Thread(target=self._mitlauscher, daemon=True).start()

        puffer = collections.deque()
        stille = 0
        in_sprache = False

        def abgeben(endgueltig):
            """Segment an den Arbeiter geben und neu anfangen.

            `endgueltig` unterscheidet ZWEI ganz verschiedene Gründe, warum ein
            Segment endet:

              * echte Stille (endgueltig=True)  -- Ramzi ist wirklich fertig
              * die Längenbegrenzung (endgueltig=False) -- er redet noch,
                das Segment wurde nur aus technischen Gründen zerschnitten

            Der Unterschied ist entscheidend. Ohne ihn wurde am 31.07.2026 eine
            zwei Minuten lange, durchgehende Äußerung in 15-Sekunden-Stücke
            gehackt, und JEDES Stück einzeln als fertiger Befehl behandelt --
            das erste enthielt "Noor" und wurde sofort (unvollständig!) an
            Noor geschickt, wonach das Fenster wieder zuging und der ganze
            Rest verloren war."""
            self._auftraege.put((list(puffer), endgueltig))
            puffer.clear()
            with self._schloss:
                self._laufend = None

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
                if qalam_nimmt_auf() or self._spricht_gerade():
                    puffer.clear()
                    in_sprache = False
                    with self._schloss:
                        self._laufend = None
                    continue

                try:
                    ist_sprache = self.vad.is_speech(frame.tobytes(), RATE)
                except Exception:
                    continue

                if ist_sprache:
                    in_sprache = True
                    stille = 0
                    puffer.append(frame)
                    # Nur einen Ausschnitt hinlegen, damit der Mitlauscher in
                    # seinem eigenen Faden etwas zu tun hat. Kopieren, nicht
                    # teilen: an der Deque wird gleich weitergearbeitet.
                    with self._schloss:
                        self._laufend = list(puffer)[-BLICK_FRAMES:]
                    if len(puffer) >= self.max_frames:
                        abgeben(endgueltig=False)   # er redet noch -- nur ein Zwischenstück
                        in_sprache = True            # bleibt in Sprache, es geht ja weiter
                elif in_sprache:
                    stille += 1
                    puffer.append(frame)
                    if stille >= self.stille_frames:
                        abgeben(endgueltig=True)      # echte Stille -- er ist fertig
                        in_sprache = False
                        stille = 0

    def _melde(self, rueckruf, *args):
        """Rückruf aufrufen, ohne dass ein Fehler darin das Ohr umbringt."""
        if not rueckruf:
            return
        try:
            rueckruf(*args)
        except Exception as e:
            print(f'[Weckwort] Rückruf fehlgeschlagen: {e}')

    def _arbeiter(self):
        """Fertige Segmente genau transkribieren -- in Ruhe, neben der Aufnahme."""
        while not self._stop.is_set():
            try:
                frames, endgueltig = self._auftraege.get(timeout=0.4)
            except queue.Empty:
                continue
            try:
                self._pruefe(frames, endgueltig)
            except Exception as e:
                print(f'[Weckwort] Auswertung fehlgeschlagen: {e}')

    def _mitlauscher(self):
        """Mithören, WÄHREND gesprochen wird -- eigener Faden, eigenes Tempo.

        Er nimmt sich immer nur den letzten Ausschnitt, den die Aufnahme
        hingelegt hat. Braucht er dafür mal länger, verzögert das die Aufnahme
        um keine Millisekunde -- genau daran ist die erste Fassung gescheitert.
        """
        erkannt = False
        while not self._stop.is_set():
            time.sleep(0.3)
            with self._schloss:
                schnipsel = self._laufend
            if not schnipsel:
                erkannt = False          # Satz vorbei, beim nächsten neu suchen
                continue
            if self.schlaeft or len(schnipsel) < 25:
                continue

            vorlaeufig = self._hoer_kurz(schnipsel)
            if not vorlaeufig:
                continue
            if not erkannt and WECKWORT.search(vorlaeufig):
                erkannt = True
                self._melde(self.beim_erkennen)
            if erkannt or time.time() < self.folge_bis:
                self._melde(self.beim_mitschreiben, vorlaeufig)

    def _hoer_kurz(self, frames):
        """Einen kurzen Ausschnitt mit dem schnellen Modell mithören.

        Immer nur ein Ausschnitt, nie der ganze Satz: die Rechenzeit soll
        gleich bleiben, egal wie lange Ramzi schon spricht."""
        if len(frames) < 20:
            return None
        audio = np.concatenate(frames).astype(np.float32) / 32768.0
        try:
            segmente, _ = self.flink.transcribe(audio, language='de', beam_size=1)
            return ' '.join(s.text for s in segmente).strip()
        except Exception:
            return None

    def _pruefe(self, puffer, endgueltig=True):
        """Segment genau transkribieren und entscheiden.

        `endgueltig=False` heißt: nur ein Zwischenstück, Ramzi redet noch
        weiter (siehe abgeben()). `beim_wecken` bekommt das mitgeteilt und
        entscheidet selbst, ob es sammelt oder ausführt -- hier wird nur
        gehört und weitergegeben, nicht bewertet."""
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
            self._melde(self.beim_wecken, text, endgueltig)


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
